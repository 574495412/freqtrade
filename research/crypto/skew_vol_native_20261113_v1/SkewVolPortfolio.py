"""Local IStrategy: annual skewness and matched low-volatility source hypotheses.

Signals, target budgets and actual native fills are separate evidence. This is
not upstream Freqtrade functionality or an exact Coinmetrics spot replication.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class _MonthlyPortfolio(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    startup_candle_count = 365
    can_short = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    factor_field = None

    def bot_start(self, **kwargs):
        path = Path(self.config["research_monthly_scores"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.config["research_scores_sha256"]:
            raise ValueError("Frozen score input changed")
        self._scores = json.loads(path.read_text())
        self._budgets = {}
        self._intents = set()
        self.research_trace = {"monthly_budgets": {}, "attempts": {}, "accepted_intents": []}

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        # Native shifts the signal from this completed daily bar into the next
        # execution day. Entry day2 uses month-start scores known on day1.
        execution = dataframe.date + pd.Timedelta(days=1)
        months = execution.dt.strftime("%Y-%m")
        target = months.map(
            lambda month: (
                self._scores["months"]
                .get(month, {})
                .get(self.factor_field, {})
                .get(metadata["pair"], 0)
            )
        )
        ready = execution.dt.day.eq(2) & dataframe.volume.gt(0)
        dataframe["enter_long"] = np.where(ready & target.eq(1), 1, 0)
        dataframe["enter_short"] = np.where(ready & target.eq(-1), 1, 0)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        # Full exit on day1, new initial orders on day2. This explicit one-day
        # cash gap avoids pretending native sequential fills are simultaneous.
        exit_day = (dataframe.date + pd.Timedelta(days=1)).dt.day.eq(1)
        dataframe["exit_long"] = exit_day.astype(int)
        dataframe["exit_short"] = exit_day.astype(int)
        return dataframe

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
        clock = pd.Timestamp(current_time)
        if clock.day != 2 or clock.hour or clock.minute or clock.second:
            return 0
        month = clock.strftime("%Y-%m")
        if month not in self._budgets:
            self._budgets[month] = self.wallets.get_total_stake_amount() / 20
            self.research_trace["monthly_budgets"][month] = self._budgets[month]
        budget = self._budgets[month]
        # Do not let native minimum-order clamping upsize a source allocation.
        reason = "ADMITTED" if budget >= (min_stake or 0) else "MINIMUM_EXCEEDS_BUDGET"
        self.research_trace["attempts"][month + "|" + pair] = {
            "clock": str(clock),
            "budget_usdt": budget,
            "minimum_stake": min_stake,
            "maximum_stake": max_stake,
            "side": side,
            "reason": reason,
        }
        return min(budget, max_stake) if reason == "ADMITTED" else 0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        clock = pd.Timestamp(current_time)
        month = clock.strftime("%Y-%m")
        key = month + "|" + pair
        budget = self._budgets.get(month)
        if (
            clock.day != 2
            or clock.hour
            or clock.minute
            or clock.second
            or budget is None
            or key in self._intents
            or amount * rate > budget + 0.01
        ):
            return False
        if any(
            pd.Timestamp(trade.open_date_utc).strftime("%Y-%m") != month
            for trade in Trade.get_open_trades()
        ):
            return False
        self._intents.add(key)
        self.research_trace["accepted_intents"].append(
            {
                "clock": str(clock),
                "pair": pair,
                "side": side,
                "quantity": amount,
                "rate": rate,
                "maximum_budget_usdt": budget,
            }
        )
        return True


class AnnualSkew(_MonthlyPortfolio):
    """Long the lowest360-day skewness quartile, short the highest quartile."""

    factor_field = "skew360"


class LowVolControl(_MonthlyPortfolio):
    """Matched clock/budget: low63-day volatility long, high volatility short."""

    factor_field = "volatility63"
