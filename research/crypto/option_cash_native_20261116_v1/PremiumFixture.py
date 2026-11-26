"""Synthetic native order fixture,no researched trading signal or performance."""

from decimal import Decimal

import pandas as pd

from freqtrade.optimize.option_fee_execution import native_option_notional
from freqtrade.strategy import IStrategy


class PremiumFixture(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 0
    stoploss = -0.99
    minimal_roi = {}
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "limit",
        "stoploss_on_exchange": False,
    }

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"], dataframe["enter_short"] = 0, 0
        if self.config.get("synthetic_option_scheduled_start"):
            dataframe["enter_short"] = (
                dataframe.date == pd.Timestamp(self.config["fixture_entry_signal"])
            ).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        if self.config.get("synthetic_option_scheduled_start"):
            dataframe["exit_short"] = (
                dataframe.date == pd.Timestamp(self.config["fixture_exit_signal"])
            ).astype(int)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, **kwargs):
        if not self.config.get("synthetic_option_scheduled_start"):
            return proposed_stake
        spec = self.wallets.contract.spec(pair)
        return native_option_notional(
            2 * spec.min_amount_base,
            Decimal(str(current_rate)),
            self.wallets._exchange.markets[pair],
            self.wallets._exchange.precisionMode,
        )
