"""Frozen synthetic decisions; never an alpha or historical source strategy."""

import pandas as pd

from freqtrade.strategy import IStrategy


class FixtureOperations(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 0
    can_short = True
    minimal_roi = {}
    stoploss = -0.99
    process_only_new_candles = True
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
        at = "2026-04-29T05:45Z" if metadata["pair"].startswith("FAKEONE") else "2026-04-29T05:55Z"
        dataframe["enter_long"] = (dataframe.date == pd.Timestamp(at)).astype(int)
        dataframe["enter_short"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        at = "2026-04-29T05:50Z" if metadata["pair"].startswith("FAKEONE") else "2026-04-29T06:05Z"
        dataframe["exit_long"] = (dataframe.date == pd.Timestamp(at)).astype(int)
        dataframe["exit_short"] = 0
        return dataframe

    def custom_entry_price(self, pair, proposed_rate, **kwargs):
        return 100.0 if pair.startswith("FAKEONE") else 100.005

    def custom_exit_price(self, pair, proposed_rate, **kwargs):
        return 103.005 if pair.startswith("FAKEONE") else 100.0
