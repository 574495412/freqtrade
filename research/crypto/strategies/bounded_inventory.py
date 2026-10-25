"""EMA-band inventory reversion with equal tranches and a fixed campaign loss cap."""

import math

import numpy as np
import pandas as pd
import talib.abstract as ta

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute


def signals(frame, small, core):
    frame = frame.copy()
    low = frame[["ema12_1h", "ema48_1h"]].min(axis=1)
    high = frame[["ema12_1h", "ema48_1h"]].max(axis=1)
    known = (
        np.isfinite(frame[["ema12_1h", "ema48_1h", "hour_atr_1h", "trend_4h", "slow_atr_4h"]]).all(
            axis=1
        )
        & frame.hour_atr_1h.gt(0)
        & frame.slow_atr_4h.gt(0)
        & frame.volume.gt(0)
    )
    small_gap = np.maximum(0.006 * frame.close, 1.5 * frame.hour_atr_1h)
    core_gap = np.maximum(0.02 * frame.close, 1.5 * frame.slow_atr_4h)
    flags = {
        "small_long": known & frame.close.gt(frame.trend_4h) & frame.close.lt(low - small_gap),
        "small_short": known & frame.close.lt(frame.trend_4h) & frame.close.gt(high + small_gap),
        "core_long": known & frame.close.gt(frame.trend_4h) & frame.close.lt(low - core_gap),
        "core_short": known & frame.close.lt(frame.trend_4h) & frame.close.gt(high + core_gap),
    }
    edges = {k: v & ~v.shift(1, fill_value=False) for k, v in flags.items()}
    frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
    if small:
        frame.loc[edges["small_long"], ["enter_long", "enter_tag"]] = [1, "small_inventory"]
        frame.loc[edges["small_short"], ["enter_short", "enter_tag"]] = [1, "small_inventory"]
    if core:
        long, short = edges["core_long"], edges["core_short"]
        frame.loc[long | short, ["enter_long", "enter_short"]] = 0
        frame.loc[long, ["enter_long", "enter_tag"]] = [1, "core_inventory"]
        frame.loc[short, ["enter_short", "enter_tag"]] = [1, "core_inventory"]
    return frame


class InventorySingle(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 800
    can_short = True
    process_only_new_candles = True
    position_adjustment_enable = False
    max_entry_position_adjustment = 0
    use_custom_stoploss = True
    use_custom_roi = True
    minimal_roi = {"0": 1000.0}
    stoploss = -0.65
    allow_small = True
    allow_core = True
    tranches = 1

    @informative("1h")
    def populate_hour(self, dataframe, metadata):
        dataframe["ema12"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema48"] = ta.EMA(dataframe, timeperiod=48)
        dataframe["hour_atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    @informative("4h")
    def populate_slow(self, dataframe, metadata):
        dataframe["trend"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["slow_atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        return signals(dataframe, self.allow_small, self.allow_core)

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("5min") - pd.Timedelta(minutes=5)
        rows = frame.loc[frame.date.eq(clock)]
        return rows.iloc[0] if len(rows) == 1 else None

    def leverage(
        self,
        pair,
        current_time,
        current_rate,
        proposed_leverage,
        max_leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        return min(2.0, max_leverage)

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if entry_tag not in {"small_inventory", "core_inventory"}:
            return False
        return sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True)) < 2

    def distance(self, row, rate, tag):
        if row is None or rate <= 0:
            return None
        small = tag == "small_inventory"
        atr = row.hour_atr_1h if small else row.slow_atr_4h
        if not math.isfinite(atr) or atr <= 0:
            return None
        floor, ceiling = (0.03, 0.10) if small else (0.08, 0.30)
        distance = max(floor, 4 * atr / rate)
        return distance if distance <= ceiling else None

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        distance = self.distance(self.closed_row(pair, current_time), current_rate, entry_tag)
        if distance is None or leverage != 2.0:
            return 0.0
        sign = -1 if side == "short" else 1
        fee = float(self.config["fee"])
        capital = float(self.wallets.get_total_stake_amount())
        small = entry_tag == "small_inventory"
        budget, margin = capital * (0.001 if small else 0.005), capital * (0.02 if small else 0.20)
        stop = current_rate * (1 - sign * distance)
        prices = [current_rate * (1 - sign * distance * i / 4) for i in range(self.tranches)]
        losses = [-sign * (stop / p - 1) + fee * (1 + stop / p) for p in prices]
        stake = min(budget / (leverage * sum(losses)), margin / self.tranches, max_stake)
        if min_stake is not None and stake < min_stake:
            return 0.0
        if not hasattr(self, "_pending"):
            self._pending = {}
        self._pending[pair] = {
            "capital": capital,
            "risk_budget": budget,
            "margin_cap": margin,
            "distance": distance,
        }
        return float(stake)

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side or trade.nr_of_successful_entries != 1:
            return
        plan = getattr(self, "_pending", {}).pop(pair, None)
        if plan is None:
            return
        sign = -1 if trade.is_short else 1
        plan.update(
            first_price=float(trade.open_rate),
            tranche_stake=float(trade.stake_amount),
            initial_stop=float(trade.open_rate * (1 - sign * plan["distance"])),
        )
        trade.set_custom_data("campaign", plan)

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        plan = trade.get_custom_data("campaign")
        if plan is None:
            return None
        return stoploss_from_absolute(
            plan["initial_stop"], current_rate, trade.is_short, trade.leverage
        )

    def custom_roi(self, pair, trade, current_time, trade_duration, entry_tag, side, **kwargs):
        return 0.016 if entry_tag == "small_inventory" else 0.06

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("campaign") is None:
            return "missing_campaign_state"
        minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        if minutes >= (240 if trade.enter_tag == "small_inventory" else 4320):
            return "campaign_time_limit"
        return None

    def adjust_trade_position(
        self,
        trade,
        current_time,
        current_rate,
        current_profit,
        min_stake,
        max_stake,
        current_entry_rate,
        current_exit_rate,
        current_entry_profit,
        current_exit_profit,
        **kwargs,
    ):
        n = trade.nr_of_successful_entries
        plan = trade.get_custom_data("campaign")
        if trade.has_open_orders or plan is None or n >= self.tranches:
            return None
        sign = -1 if trade.is_short else 1
        stop = plan["initial_stop"]
        level = plan["first_price"] * (1 - sign * plan["distance"] * n / 4)
        if sign * (current_entry_rate - stop) <= 0 or sign * (current_entry_rate - level) > 0:
            return None
        row = self.closed_row(trade.pair, current_time)
        if row is None or sign * (row.close - level) > 0:
            return None
        fee = float(self.config["fee"])
        # Ignore positive accrued funding as risk credit; include known negative funding.
        loss_now = trade.amount * (
            -sign * (stop - trade.open_rate) + fee * (stop + trade.open_rate)
        )
        loss_now -= min(float(trade.funding_fees or 0), 0)
        loss_per_stake = trade.leverage * (
            -sign * (stop / current_entry_rate - 1) + fee * (1 + stop / current_entry_rate)
        )
        if loss_per_stake <= 0:
            return None
        stake = min(
            plan["tranche_stake"],
            plan["margin_cap"] - trade.stake_amount,
            (plan["risk_budget"] - max(loss_now, 0)) / loss_per_stake,
            max_stake,
        )
        if stake <= 0 or (min_stake is not None and stake < min_stake):
            return None
        return float(stake), f"equal_tranche_{n + 1}"


class InventoryGrid3(InventorySingle):
    position_adjustment_enable = True
    max_entry_position_adjustment = 2
    tranches = 3


class InventorySmallGrid3(InventoryGrid3):
    allow_core = False


class InventoryCoreGrid3(InventoryGrid3):
    allow_small = False
