"""Preserve the 2022 Supertrend formula using arrays compatible with pandas copy-on-write."""

import numpy as np
import pandas as pd
import talib.abstract as ta
from Supertrend2022Base import Supertrend2022Base


class Supertrend2022(Supertrend2022Base):
    """Original fixed rules and parameters, 800-hour warmup, native default 1x long-only."""

    startup_candle_count = 800

    def supertrend(self, dataframe, multiplier, period):
        close = dataframe["close"].to_numpy()
        average_range = ta.SMA(ta.TRANGE(dataframe), period)
        midpoint = (dataframe["high"].to_numpy() + dataframe["low"].to_numpy()) / 2
        upper, lower = midpoint + multiplier * average_range, midpoint - multiplier * average_range
        final_upper = np.zeros(len(dataframe))
        final_lower = np.zeros(len(dataframe))
        trend = np.zeros(len(dataframe))
        for i in range(period, len(dataframe)):
            final_upper[i] = (
                upper[i]
                if upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]
                else final_upper[i - 1]
            )
            final_lower[i] = (
                lower[i]
                if lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]
                else final_lower[i - 1]
            )
        for i in range(period, len(dataframe)):
            previous = trend[i - 1]
            if previous == final_upper[i - 1] and close[i] <= final_upper[i]:
                trend[i] = final_upper[i]
            elif (previous == final_upper[i - 1] and close[i] > final_upper[i]) or (
                previous == final_lower[i - 1] and close[i] >= final_lower[i]
            ):
                trend[i] = final_lower[i]
            elif previous == final_lower[i - 1] and close[i] < final_lower[i]:
                trend[i] = final_upper[i]
        direction = np.where(trend > 0, np.where(close < trend, "down", "up"), "nan")
        return pd.DataFrame({"ST": trend, "STX": direction}, index=dataframe.index)
