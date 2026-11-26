"""Explicit synthetic amount-rule lifecycle, no strategy-profit claim."""

import pandas as pd

from freqtrade.strategy import IStrategy


class FixtureAmount(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 0
    can_short = True
    minimal_roi = {}
    stoploss = -0.99
    position_adjustment_enable = True
    max_entry_position_adjustment = 1
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        times = [pd.Timestamp("2026-04-29T05:45Z")]
        if self.config["fixture_case"] == "notional_fall":
            times.append(pd.Timestamp("2026-04-29T05:55Z"))
        dataframe["enter_long"] = dataframe.date.isin(times).astype(int)
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = (dataframe.date == pd.Timestamp("2026-04-29T06:05Z")).astype(int)
        dataframe["exit_short"] = 0
        return dataframe

    def custom_stake_amount(self, **kwargs):
        return {
            "fine_DCA": 100,
            "notional_fall": 60,
            "post_round": 54,
            "coarse_dust": 60,
            "unknown": 60,
        }[self.config["fixture_case"]]

    def custom_entry_price(self, proposed_rate, **kwargs):
        return 100.0

    def custom_exit_price(self, proposed_rate, **kwargs):
        return 100.0

    def adjust_trade_position(self, trade, current_time, **kwargs):
        if self.config["fixture_case"] != "fine_DCA":
            return None
        if (
            current_time == pd.Timestamp("2026-04-29T06:01Z")
            and trade.nr_of_successful_entries == 1
        ):
            return 60.0
        if current_time == pd.Timestamp("2026-04-29T06:05Z") and trade.nr_of_successful_exits == 0:
            return -120.0
        return None
