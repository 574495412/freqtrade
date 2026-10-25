"""Research-only core component: frozen forecasts, fixed initial-dollar risk and full exits."""

import math
from pathlib import Path

import pandas as pd
from native_absorption import ReboundPriceControl

from freqtrade.persistence import Trade
from freqtrade.research.data import sha


class InteractionCoreBase(ReboundPriceControl):
    policy = "LinearStatic"
    use_custom_roi = False
    stoploss = -0.99
    minimal_roi = {"0": 1000.0}

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Core component is an unqualified retrospective research diagnostic")
        symbol = metadata["pair"].split("/")[0] + "USDT"
        record = self.config["research_core_component_panels"][self.policy]
        if not hasattr(self, "_core_panels"):
            self._core_panels = {}
        if symbol not in self._core_panels:
            path = Path(record["path"]) / f"{symbol}.parquet"
            if sha(path) != record["hashes"][symbol]:
                raise ValueError("Frozen core forecast changed")
            self._core_panels[symbol] = pd.read_parquet(path)
        return dataframe.merge(
            self._core_panels[symbol], on="date", how="left", validate="one_to_one"
        )

    def populate_entry_trend(self, dataframe, metadata):
        frame = dataframe.copy()
        frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
        frame.loc[frame.forecast_side.eq(1), ["enter_long", "enter_tag"]] = [1, "core_interaction"]
        frame.loc[frame.forecast_side.eq(-1), ["enter_short", "enter_tag"]] = [
            1,
            "core_interaction",
        ]
        return frame

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        return (
            self.dp.runmode.value == "backtest"
            and entry_tag == "core_interaction"
            and side in {"long", "short"}
            and len(Trade.get_trades_proxy(is_open=True)) < 2
        )

    def distance(self, row, rate, tag):
        if tag != "core_interaction" or row is None or rate <= 0:
            return None
        atr = row.feature_atr
        if not math.isfinite(atr) or atr <= 0:
            return None
        value = max(0.015, 6 * atr / rate)
        return value if value <= 0.12 else None

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
        risk, margin = reference * 0.005, reference * 0.20
        sign, fee = (-1 if side == "short" else 1), float(self.config["fee"])
        loss = distance + fee * (2 - sign * distance)
        stake = min(0.99 * risk / (leverage * loss), margin, max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("initial_stop") is None:
            return "missing_core_interaction_state"
        if (current_time - trade.open_date_utc).total_seconds() >= 24 * 3600:
            return "core_interaction_24h"
        return None


class InteractionCoreLinear(InteractionCoreBase):
    policy = "LinearStatic"


class InteractionCoreStatic(InteractionCoreBase):
    policy = "BoostStatic"


class InteractionCoreMonthly(InteractionCoreBase):
    policy = "BoostMonthly"
