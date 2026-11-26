"""Local missingness adapter and matched controls for the pinned OI reference."""

import numpy as np
from OpenInterestReference import OpenInterestReference

from freqtrade.strategy import informative


class _OIKnown(OpenInterestReference):
    @informative("1h", candle_type="open_interest", ffill=False)
    def populate_indicators_oi_1h(self, dataframe, metadata):
        result = super().populate_indicators_oi_1h(dataframe, metadata)
        known = result.open_interest_amount.notna() & result.open_interest_amount.gt(0)
        result["oi_window_known"] = known.rolling(6, min_periods=6).sum().eq(6)
        return result

    def price_signals(self, dataframe, known):
        dataframe["enter_long"] = np.where(known & dataframe.close.gt(dataframe.hh_20), 1, 0)
        dataframe["enter_short"] = np.where(known & dataframe.close.lt(dataframe.ll_20), 1, 0)
        return dataframe


class OIConfirmedBreakout(_OIKnown):
    def populate_entry_trend(self, dataframe, metadata):
        result = super().populate_entry_trend(dataframe, metadata)
        known = result.oi_window_known_1h.fillna(False)
        for field in ["enter_long", "enter_short"]:
            result[field] = np.where(known, result[field].fillna(0), 0)
        return result


class PriceBreakoutAvailable(_OIKnown):
    def populate_entry_trend(self, dataframe, metadata):
        return self.price_signals(dataframe, dataframe.oi_window_known_1h.fillna(False))


class PriceBreakoutFull(_OIKnown):
    def populate_entry_trend(self, dataframe, metadata):
        return self.price_signals(dataframe, True)
