"""Executable price rebound controls and delayed real-flow absorption hypotheses."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from freqtrade.strategy import IStrategy, stoploss_from_absolute


REQUIRED = [
    "return5",
    "return60",
    "close_location",
    "volume_ratio",
    "atr_fraction",
    "flow20",
    "impact_residual20",
    "oi_contraction60",
    "global_account_logratio",
]


def events(frame):
    """All treatments use exactly the same known feature population and price event edges."""
    frame = frame.copy()
    known = np.isfinite(frame[REQUIRED]).all(axis=1) & frame.atr_fraction.gt(0)
    long = (
        known
        & frame.return60.lt(-math.log(1.01))
        & frame.return5.gt(0)
        & frame.close_location.ge(0.5)
        & frame.volume_ratio.ge(1.5)
    )
    short = (
        known
        & frame.return60.gt(math.log(1.01))
        & frame.return5.lt(0)
        & frame.close_location.le(-0.5)
        & frame.volume_ratio.ge(1.5)
    )
    frame["price_long"] = long & ~long.shift(1, fill_value=False)
    frame["price_short"] = short & ~short.shift(1, fill_value=False)
    frame["flow_long"] = frame.price_long & frame.flow20.le(-0.20) & frame.impact_residual20.ge(0)
    frame["flow_short"] = frame.price_short & frame.flow20.ge(0.20) & frame.impact_residual20.le(0)
    frame["contraction_long"] = frame.flow_long & frame.oi_contraction60.ge(0.01)
    frame["contraction_short"] = frame.flow_short & frame.oi_contraction60.ge(0.01)
    frame["crowding_long"] = frame.contraction_long & frame.global_account_logratio.ge(
        math.log(1.2)
    )
    frame["crowding_short"] = frame.contraction_short & frame.global_account_logratio.le(
        -math.log(1.2)
    )
    return frame


class ReboundPriceControl(IStrategy):
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
    small_filter = "price"
    allow_small = True
    allow_core = False

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Historical absorption inputs permit research backtests only")
        symbol = metadata["pair"].split("/")[0] + "USDT"
        path = Path(self.config["research_absorption_panel"]) / f"{symbol}.parquet"
        if sha(path) != self.config["research_absorption_panel_hashes"][symbol]:
            raise ValueError("Registered causal event panel changed")
        panel = pd.read_parquet(path)
        return dataframe.merge(panel, on="date", how="left", validate="one_to_one")

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = dataframe.copy()
        dataframe["enter_long"], dataframe["enter_short"], dataframe["enter_tag"] = 0, 0, ""
        if self.allow_small:
            long = dataframe[self.small_filter + "_long"].eq(True)
            short = dataframe[self.small_filter + "_short"].eq(True)
            dataframe.loc[long, ["enter_long", "enter_tag"]] = [1, "small_absorption"]
            dataframe.loc[short, ["enter_short", "enter_tag"]] = [1, "small_absorption"]
        if self.allow_core:
            long, short = dataframe.contraction_long.eq(True), dataframe.contraction_short.eq(True)
            dataframe.loc[long | short, ["enter_long", "enter_short"]] = 0
            dataframe.loc[long, ["enter_long", "enter_tag"]] = [1, "core_absorption"]
            dataframe.loc[short, ["enter_short", "enter_tag"]] = [1, "core_absorption"]
        return dataframe

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
        if entry_tag not in {"small_absorption", "core_absorption"}:
            return False
        return sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True)) < 2

    def distance(self, row, rate, tag):
        if row is None or rate <= 0 or not math.isfinite(row.feature_atr) or row.feature_atr <= 0:
            return None
        small = tag == "small_absorption"
        floor, multiple, ceiling = (0.008, 2, 0.025) if small else (0.015, 3, 0.05)
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
        distance = self.distance(self.closed_row(pair, current_time), current_rate, entry_tag)
        if distance is None or leverage != 2.0:
            return 0.0
        small = entry_tag == "small_absorption"
        risk, margin = (0.0005, 0.02) if small else (0.0025, 0.10)
        sign = -1 if side == "short" else 1
        fee = float(self.config["fee"])
        loss = distance + fee + fee * (1 - sign * distance)
        capital = self.wallets.get_total_stake_amount()
        stake = min(capital * margin, capital * risk / (loss * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side:
            return
        distance = self.distance(
            self.closed_row(pair, trade.open_date_utc), trade.open_rate, trade.enter_tag
        )
        if distance is not None:
            sign = -1 if trade.is_short else 1
            trade.set_custom_data("initial_stop", trade.open_rate * (1 - sign * distance))

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        stop = trade.get_custom_data("initial_stop")
        return (
            None
            if stop is None
            else stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)
        )

    def custom_roi(self, pair, trade, current_time, trade_duration, entry_tag, side, **kwargs):
        return 0.016 if entry_tag == "small_absorption" else 0.10

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("initial_stop") is None:
            return "missing_risk_state"
        minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        if minutes >= (240 if trade.enter_tag == "small_absorption" else 1440):
            return "absorption_time_limit"
        return None


class ReboundFlow(ReboundPriceControl):
    small_filter = "flow"


class ReboundContraction(ReboundPriceControl):
    small_filter = "contraction"


class ReboundCrowding(ReboundPriceControl):
    small_filter = "crowding"


class AbsorptionCore(ReboundPriceControl):
    allow_small = False
    allow_core = True


class AbsorptionDual(ReboundPriceControl):
    small_filter = "flow"
    allow_core = True
