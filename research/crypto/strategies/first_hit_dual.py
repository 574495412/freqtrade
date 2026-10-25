"""Research-only complete-position profit checkpoints, hard stops and finite holding caps."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from freqtrade.strategy import IStrategy
from research.crypto.first_hit_paths import SPECS


class FirstHitDualBase(IStrategy):
    INTERFACE_VERSION = 3
    policy = "LinearStatic"
    timeframe = "5m"
    startup_candle_count = 24
    can_short = True
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False
    use_custom_stoploss = False
    use_custom_roi = False
    stoploss = -0.99
    minimal_roi = {"0": 1000.0}

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Unqualified first-hit research permits historical backtests only")
        symbol = metadata["pair"].split("/")[0] + "USDT"
        record = self.config["research_first_hit_panels"][self.policy]
        if not hasattr(self, "_first_hit_panels"):
            self._first_hit_panels = {}
        if symbol not in self._first_hit_panels:
            path = Path(record["path"]) / f"{symbol}.parquet"
            if sha(path) != record["hashes"][symbol]:
                raise ValueError("Frozen first-hit forecast changed")
            self._first_hit_panels[symbol] = pd.read_parquet(path)
        return dataframe.merge(
            self._first_hit_panels[symbol], on="date", how="left", validate="one_to_one"
        )

    def populate_entry_trend(self, dataframe, metadata):
        frame = dataframe.copy()
        frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
        for sleeve in SPECS:
            eligible = frame.forecast_sleeve.eq(sleeve)
            for side, column in [(1, "enter_long"), (-1, "enter_short")]:
                frame.loc[eligible & frame.forecast_side.eq(side), [column, "enter_tag"]] = [
                    1,
                    f"{sleeve}_first_hit",
                ]
        return frame

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        previous = pd.Timestamp(when).floor("5min") - pd.Timedelta(minutes=5)
        rows = frame.loc[frame.date.eq(previous)]
        return rows.iloc[0] if len(rows) == 1 else None

    def distance(self, row, rate, tag):
        sleeve = tag.removesuffix("_first_hit") if isinstance(tag, str) else ""
        if row is None or sleeve not in SPECS or rate <= 0:
            return None
        if not np.isfinite(row.feature_atr) or row.feature_atr <= 0:
            return None
        spec = SPECS[sleeve]
        value = max(spec["floor"], spec["atr_multiple"] * row.feature_atr / rate)
        return value if value <= spec["ceiling"] else None

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, **kwargs):
        return min(2.0, max_leverage)

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        return (
            self.dp.runmode.value == "backtest"
            and entry_tag in {"small_first_hit", "core_first_hit"}
            and side in {"long", "short"}
            and sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True)) < 2
        )

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
        if distance is None or leverage != 2 or side not in {"long", "short"}:
            return 0.0
        reference = float(self.config["research_initial_equity_usdt"]) * 0.99
        risk, cap = (0.001, 0.04) if entry_tag == "small_first_hit" else (0.005, 0.20)
        sign, fee = (-1 if side == "short" else 1), float(self.config["fee"])
        loss = distance + fee * (2 - sign * distance)
        stake = min(0.99 * reference * risk / (2 * loss), reference * cap, max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side:
            return
        if trade.get_custom_data("locked_stop") is not None:
            raise ValueError("This lifecycle does not permit additional entries")
        distance = self.distance(
            self.closed_row(pair, trade.open_date_utc), trade.open_rate, trade.enter_tag
        )
        if distance is None:
            return
        spec = SPECS[trade.enter_tag.removesuffix("_first_hit")]
        sign = -1 if trade.is_short else 1
        risk = distance + trade.fee_open + trade.fee_close * (1 - sign * distance)
        # One native adjustment after the entry fill. No repeated stop reconstruction.
        trade.adjust_stop_loss(trade.open_rate, distance * trade.leverage, allow_refresh=True)
        trade.set_custom_data("locked_stop", trade.stop_loss)
        trade.set_custom_data(
            "target_roi", spec["target_r"] * risk * trade.leverage / (1 + sign * trade.fee_open)
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        stop, target = trade.get_custom_data("locked_stop"), trade.get_custom_data("target_roi")
        if stop is None or target is None:
            return "missing_first_hit_state"
        spec = SPECS[trade.enter_tag.removesuffix("_first_hit")]
        if (current_time - trade.open_date_utc).total_seconds() >= spec["hours"] * 3600:
            return "first_hit_time"
        sign = -1 if trade.is_short else 1
        if sign * (current_rate - stop) <= 0:
            return "first_hit_gap"
        if current_profit > target:
            return "first_hit_profit"
        return None


class FirstHitDualLinear(FirstHitDualBase):
    policy = "LinearStatic"


class FirstHitDualStatic(FirstHitDualBase):
    policy = "BoostStatic"


class FirstHitDualMonthly(FirstHitDualBase):
    policy = "BoostMonthly"
