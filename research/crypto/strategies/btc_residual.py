"""Directional perpetual accounts using a BTC residual feature; no hedge is claimed."""

import math
from pathlib import Path

import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class BTCResidualRaw(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 24
    can_short = True
    process_only_new_candles = True
    position_adjustment_enable = False
    use_custom_stoploss = True
    use_custom_roi = True
    stoploss = -0.15
    minimal_roi = {"0": 1000.0}
    stability_filter = False
    trend_filter = False
    momentum_core = False

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Frozen historical residual panels permit research backtests only")
        symbol = metadata["pair"].split("/")[0] + "USDT"
        path = Path(self.config["research_residual_panel"]) / f"{symbol}.parquet"
        if sha(path) != self.config["research_residual_panel_hashes"][symbol]:
            raise ValueError("Residual feature panel identity changed")
        return dataframe.merge(pd.read_parquet(path), on="date", how="left", validate="one_to_one")

    def populate_entry_trend(self, dataframe, metadata):
        frame = dataframe.copy()
        frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
        for sleeve in ["small", "core"]:
            long, short = frame[f"{sleeve}_long"].eq(True), frame[f"{sleeve}_short"].eq(True)
            if self.momentum_core and sleeve == "core":
                long, short = short, long
            if self.stability_filter:
                long &= frame[f"stable_{sleeve}"].eq(True)
                short &= frame[f"stable_{sleeve}"].eq(True)
            if self.trend_filter or (self.momentum_core and sleeve == "core"):
                long &= frame.regime_long.eq(True)
                short &= frame.regime_short.eq(True)
            frame.loc[long | short, ["enter_long", "enter_short"]] = 0
            frame.loc[long, ["enter_long", "enter_tag"]] = [1, f"{sleeve}_residual"]
            frame.loc[short, ["enter_short", "enter_tag"]] = [1, f"{sleeve}_residual"]
        return frame

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
        if entry_tag not in {"small_residual", "core_residual"}:
            return False
        return sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True)) < 2

    @staticmethod
    def distance(row, rate, tag):
        if row is None or rate <= 0 or not math.isfinite(row.feature_atr) or row.feature_atr <= 0:
            return None
        floor, multiple, ceiling = (0.008, 2, 0.03) if tag == "small_residual" else (0.015, 6, 0.06)
        value = max(floor, multiple * row.feature_atr / rate)
        return value if value <= ceiling else None

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
        row = self.closed_row(pair, current_time)
        distance = self.distance(row, current_rate, entry_tag)
        if distance is None or leverage != 2.0:
            return 0.0
        sign = -1 if side == "short" else 1
        fee = float(self.config["fee"])
        loss = distance + fee * (2 - sign * distance)
        small = entry_tag == "small_residual"
        capital = self.wallets.get_total_stake_amount()
        risk, margin = (0.001, 0.02) if small else (0.005, 0.20)
        stake = min(capital * risk / (loss * leverage), capital * margin, max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side:
            return
        row = self.closed_row(pair, trade.open_date_utc)
        distance = self.distance(row, trade.open_rate, trade.enter_tag)
        if distance is None:
            return
        sign = -1 if trade.is_short else 1
        gap = abs(float(row.residual)) / 2
        target_rate = trade.open_rate * math.exp(sign * gap)
        trade.set_custom_data(
            "residual_entry",
            {
                "initial_stop": trade.open_rate * (1 - sign * distance),
                "target_roi": trade.calculate_profit(target_rate).profit_ratio,
                "entry_residual": float(row.residual),
            },
        )

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        state = trade.get_custom_data("residual_entry")
        return (
            None
            if state is None
            else stoploss_from_absolute(
                state["initial_stop"], current_rate, trade.is_short, trade.leverage
            )
        )

    def custom_roi(self, pair, trade, current_time, trade_duration, entry_tag, side, **kwargs):
        state = trade.get_custom_data("residual_entry")
        return None if state is None else state["target_roi"]

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("residual_entry") is None:
            return "missing_residual_state"
        minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        if minutes >= (240 if trade.enter_tag == "small_residual" else 1440):
            return "residual_time_limit"
        return None


class BTCResidualStable(BTCResidualRaw):
    stability_filter = True


class BTCResidualTrend(BTCResidualStable):
    trend_filter = True


class BTCResidualDualMomentum(BTCResidualStable):
    momentum_core = True
