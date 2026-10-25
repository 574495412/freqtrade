"""Finite, causal range-reclaim and cross-asset momentum experiments."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from freqtrade.strategy import IStrategy, stoploss_from_absolute


def range_features(frame):
    frame = frame.copy()
    previous = frame.close.shift(1)
    frame["atr"] = (
        pd.concat(
            [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
            axis=1,
        )
        .max(axis=1)
        .rolling(14)
        .mean()
    )
    frame["prior_low"] = frame.low.rolling(32).min().shift(1)
    frame["prior_high"] = frame.high.rolling(32).max().shift(1)
    span = (frame.high - frame.low).replace(0, np.nan)
    frame["lower_wick"] = (frame[["open", "close"]].min(axis=1) - frame.low) / span
    frame["upper_wick"] = (frame.high - frame[["open", "close"]].max(axis=1)) / span
    frame["volume_ratio"] = frame.volume / frame.volume.shift(1).rolling(24).mean()
    return frame


def entry_signals(frame, small, core):
    frame = frame.copy()
    frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
    if small:
        valid = (
            frame.volume_ratio.ge(1.5)
            & (frame.atr / frame.close).between(0.001, 0.01)
            & frame.complete_universe.eq(True)
        )
        long = (
            valid
            & frame.low.lt(frame.prior_low)
            & frame.close.gt(frame.prior_low)
            & frame.close.gt(frame.open)
            & frame.lower_wick.ge(0.5)
            & frame.btc_return_24h.gt(-0.05)
        )
        short = (
            valid
            & frame.high.gt(frame.prior_high)
            & frame.close.lt(frame.prior_high)
            & frame.close.lt(frame.open)
            & frame.upper_wick.ge(0.5)
            & frame.btc_return_24h.lt(0.05)
        )
        frame.loc[long, ["enter_long", "enter_tag"]] = [1, "small_range_reclaim"]
        frame.loc[short, ["enter_short", "enter_tag"]] = [1, "small_range_reclaim"]
    if core:
        daily = frame.date.dt.hour.eq(0) & frame.date.dt.minute.eq(0)
        long = daily & frame.target_long.eq(True)
        short = daily & frame.target_short.eq(True)
        frame.loc[long | short, ["enter_long", "enter_short"]] = 0
        frame.loc[long, ["enter_long", "enter_tag"]] = [1, "core_relative_momentum"]
        frame.loc[short, ["enter_short", "enter_tag"]] = [1, "core_relative_momentum"]
    return frame


class SweepRotation(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    startup_candle_count = 96
    can_short = True
    process_only_new_candles = True
    position_adjustment_enable = False
    use_custom_stoploss = True
    stoploss = -0.30
    minimal_roi = {"0": 1000.0}
    allow_small = True
    allow_core = True

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Frozen historical panel permits research backtests only")
        if not hasattr(self, "_rank_panel"):
            path = Path(self.config["research_momentum_panel"])
            if sha(path) != self.config["research_momentum_panel_sha256"]:
                raise ValueError("Momentum panel changed")
            self._rank_panel = pd.read_parquet(path)
        symbol = metadata["pair"].split("/")[0] + "USDT"
        panel = self._rank_panel.loc[self._rank_panel.symbol.eq(symbol)].drop(columns="symbol")
        frame = range_features(dataframe)
        frame["available_at"] = frame.date + pd.Timedelta(minutes=15)
        return pd.merge_asof(
            frame.sort_values("available_at"),
            panel.sort_values("available_at"),
            on="available_at",
            tolerance=pd.Timedelta(minutes=59),
            direction="backward",
        )

    def populate_entry_trend(self, dataframe, metadata):
        return entry_signals(dataframe, self.allow_small, self.allow_core)

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("15min") - pd.Timedelta(minutes=15)
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
        if entry_tag not in {"small_range_reclaim", "core_relative_momentum"}:
            return False
        count = sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True))
        return count < (2 if entry_tag == "small_range_reclaim" else 3)

    def distance(self, row, rate, tag):
        if row is None or rate <= 0:
            return None
        atr = row.atr if tag == "small_range_reclaim" else row.hourly_atr
        if not math.isfinite(atr) or atr <= 0:
            return None
        floor, ceiling = (0.01, 0.03) if tag == "small_range_reclaim" else (0.02, 0.12)
        distance = max(floor, 2 * atr / rate)
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
        small = entry_tag == "small_range_reclaim"
        risk, margin = (0.0005, 0.02) if small else (0.005, 0.20)
        sign = -1 if side == "short" else 1
        fee = float(self.config["fee"])
        loss_fraction = distance + fee + fee * (1 - sign * distance)
        capital = self.wallets.get_total_stake_amount()
        stake = min(capital * margin, capital * risk / (loss_fraction * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side:
            return
        row = self.closed_row(pair, trade.open_date_utc)
        distance = self.distance(row, trade.open_rate, trade.enter_tag)
        if distance is not None:
            sign = -1 if trade.is_short else 1
            trade.set_custom_data("initial_stop", trade.open_rate * (1 - sign * distance))
            if trade.enter_tag == "small_range_reclaim":
                trade.set_custom_data("price_target", max(0.006, row.atr / trade.open_rate))

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        stop = trade.get_custom_data("initial_stop")
        return (
            None
            if stop is None
            else stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("initial_stop") is None:
            return "missing_risk_state"
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60
        if trade.enter_tag == "small_range_reclaim":
            sign = -1 if trade.is_short else 1
            if sign * (current_rate / trade.open_rate - 1) >= trade.get_custom_data("price_target"):
                return "reclaim_target"
            if elapsed >= 240:
                return "reclaim_time_limit"
        elif current_time.hour == 0 and current_time.minute == 15:
            row = self.closed_row(pair, current_time)
            target = (
                None if row is None else (row.target_short if trade.is_short else row.target_long)
            )
            if target is None or pd.isna(target) or not bool(target):
                return "daily_rank_dropped"
            if elapsed >= 7 * 1440:
                return "seven_day_time_limit"
        return None


class RangeReclaimOnly(SweepRotation):
    allow_core = False


class RelativeMomentumOnly(SweepRotation):
    allow_small = False
