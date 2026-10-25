"""One frozen trend-strength admission hypothesis; existing channel exits and risk stay fixed."""

import talib.abstract as ta
from daily_donchian_risk import Donchian20HalfRisk

from freqtrade.strategy import informative


class Donchian20ADX25(Donchian20HalfRisk):
    @informative("1d")
    def populate_indicators_daily(self, dataframe, metadata):
        result = super().populate_indicators_daily(dataframe, metadata)
        result["trend_quality_adx"] = ta.ADX(result, timeperiod=14)
        return result

    def populate_indicators(self, dataframe, metadata):
        result = super().populate_indicators(dataframe, metadata)
        # Same completed-day publication clock as the inherited channel/ATR levels.
        result["trend_quality_adx_1d"] = result.trend_quality_adx_1d.shift(1)
        return result

    def populate_entry_trend(self, dataframe, metadata):
        result = super().populate_entry_trend(dataframe, metadata)
        allowed = result.trend_quality_adx_1d.ge(25)
        result["enter_long"] &= allowed
        result["enter_short"] &= allowed
        result["enter_tag"] = "daily_channel_20_10_adx25"
        return result
