"""Fixed BTC market context increments to the unchanged bounded inventory rule."""

import numpy as np
from bounded_inventory import InventoryGrid3

from freqtrade.strategy import informative


class InventoryBTCTrend(InventoryGrid3):
    use_market_shock_guard = False

    @informative("4h", "BTC/USDT:USDT", fmt="btc_{column}_{timeframe}")
    def populate_btc_slow(self, dataframe, metadata):
        # Use the identical native EMA/ATR definition as the own-asset slow context.
        return self.populate_slow(dataframe, metadata)

    @informative("1h", "BTC/USDT:USDT", fmt="btc_{column}_{timeframe}")
    def populate_btc_hour(self, dataframe, metadata):
        dataframe = self.populate_hour(dataframe, metadata)
        fraction = dataframe.hour_atr / dataframe.close
        dataframe["volatility_multiple"] = fraction / fraction.shift(1).rolling(168).median()
        dataframe["shock_multiple"] = np.log(
            dataframe.close / dataframe.close.shift(1)
        ).abs() / fraction.shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        long = dataframe.btc_close_4h.gt(dataframe.btc_trend_4h)
        short = dataframe.btc_close_4h.lt(dataframe.btc_trend_4h)
        if self.use_market_shock_guard:
            stable = dataframe.btc_volatility_multiple_1h.le(
                2
            ) & dataframe.btc_shock_multiple_1h.le(2)
            long &= stable
            short &= stable
        dataframe["enter_long"] = (dataframe.enter_long.eq(1) & long).astype(int)
        dataframe["enter_short"] = (dataframe.enter_short.eq(1) & short).astype(int)
        dataframe.loc[dataframe.enter_long.eq(0) & dataframe.enter_short.eq(0), "enter_tag"] = ""
        return dataframe


class InventoryBTCStable(InventoryBTCTrend):
    use_market_shock_guard = True
