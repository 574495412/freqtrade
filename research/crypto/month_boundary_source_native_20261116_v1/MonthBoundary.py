"""Local native shared BTC/ETH wallet for fixed source calendar windows."""

import math

import pandas as pd
from MonthCalendar import month_boundary_state
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class MonthBoundary(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    startup_candle_count = 0
    can_short = False
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    last, first = 3, 2
    hold = False

    def ft_bot_start(self, **kwargs):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or self.config.get("dry_run") is not True
            or self.config.get("research_month_boundary") is not True
            or self.config.get("trading_mode") != "spot"
            or self.config["exchange"]["pair_whitelist"] != ["BTC/USDT", "ETH/USDT"]
            or self.config.get("max_open_trades") != 2
            or self.config.get("position_adjustment_enable")
        ):
            raise ValueError("Isolated two-slot shared spot research required")
        self._fatal_error, self._budgets = None, {}
        self.research_trace = {"budgets": [], "confirmed": []}
        return super().ft_bot_start(**kwargs)

    def populate_indicators(self, dataframe, metadata):
        # Calendar at the completion of this hourly candle is already known.
        # Ordinary native conversion lags its flags one row to that next open.
        clocks = pd.DatetimeIndex(dataframe.date) + pd.Timedelta(hours=1)
        dataframe["calendar_long_next_open"] = (
            True
            if self.hold
            else month_boundary_state(clocks, last=self.last, first=self.first).to_numpy()
        )
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = dataframe.calendar_long_next_open.astype(int)
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = (~dataframe.calendar_long_next_open).astype(int)
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = "source_calendar_window_end"
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
        if (
            side != "long"
            or leverage != 1
            or min_stake is None
            or not all(math.isfinite(v) and v >= 0 for v in (proposed_stake, min_stake, max_stake))
        ):
            self._fatal_error = "Unknown actual native slot budget"
            return 0.0
        budget = min(proposed_stake, max_stake)
        accepted = budget >= min_stake
        self._budgets[(pair, pd.Timestamp(current_time))] = budget if accepted else 0.0
        self.research_trace["budgets"].append(
            dict(
                pair=pair,
                clock=str(current_time),
                proposed_stake=proposed_stake,
                maximum=max_stake,
                minimum=min_stake,
                admitted=accepted,
                requested_stake=budget,
            )
        )
        return budget if accepted else 0.0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        budget = self._budgets.get((pair, pd.Timestamp(current_time)))
        if budget is None or budget <= 0 or side != "long" or amount * rate > budget + 1e-7:
            self._fatal_error = "Actual entry differs from native admitted slot budget"
            return False
        self.research_trace["confirmed"].append(
            dict(pair=pair, clock=str(current_time), amount=amount, rate=rate, budget=budget)
        )
        return True


class Calendar32(MonthBoundary):
    pass


class Calendar23(MonthBoundary):
    last, first = 2, 3


class HoldMatched(MonthBoundary):
    hold = True
