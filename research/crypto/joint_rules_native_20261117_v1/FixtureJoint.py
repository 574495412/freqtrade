"""Frozen synthetic joint lifecycle; no historical strategy or profit claim."""

import pandas as pd

from freqtrade.strategy import IStrategy


class FixtureJoint(IStrategy):
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
        signal = "05:35" if metadata["pair"].startswith("FAKEONE") else "05:55"
        dataframe["enter_long"] = (dataframe.date == pd.Timestamp(f"2026-04-29T{signal}Z")).astype(
            int
        )
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = (dataframe.date == pd.Timestamp("2026-04-29T06:05Z")).astype(int)
        dataframe["exit_short"] = 0
        return dataframe

    def custom_stake_amount(self, pair, **kwargs):
        return 100.0 if pair.startswith("FAKEONE") else 60.0

    def custom_entry_price(self, pair, **kwargs):
        return 100.0 if pair.startswith("FAKEONE") else 100.005

    def custom_exit_price(self, exit_tag, **kwargs):
        return 103.005 if exit_tag == "joint_partial" else 100.0

    def adjust_trade_position(self, trade, current_time, **kwargs):
        if not trade.pair.startswith("FAKEONE"):
            return None
        if (
            current_time == pd.Timestamp("2026-04-29T05:51Z")
            and trade.nr_of_successful_entries == 1
        ):
            return 60.0, "joint_dca"
        if current_time == pd.Timestamp("2026-04-29T05:55Z") and trade.nr_of_successful_exits == 0:
            return -120.0, "joint_partial"
        return None
