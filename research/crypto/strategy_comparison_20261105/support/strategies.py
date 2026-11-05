"""首轮固定经济机制；参数在读取研究结果前登记。"""

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class CryptoTrend(IStrategy):
    """小时级趋势跟随；支持永续多空，禁止加仓摊平。"""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 240
    stoploss = -0.08
    minimal_roi = {}
    trailing_stop = False
    position_adjustment_enable = False
    process_only_new_candles = True
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast"] = ta.EMA(dataframe, timeperiod=24)
        dataframe["slow"] = ta.EMA(dataframe, timeperiod=96)
        dataframe["regime"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["upper"] = dataframe["high"].rolling(72).max().shift(1)
        dataframe["lower"] = dataframe["low"].rolling(72).min().shift(1)
        dataframe["exit_low"] = dataframe["low"].rolling(24).min().shift(1)
        dataframe["exit_high"] = dataframe["high"].rolling(24).max().shift(1)
        dataframe["mean"] = dataframe["close"].rolling(20).mean()
        dataframe["std"] = dataframe["close"].rolling(20).std()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["fast"] > dataframe["slow"])
            & (dataframe["fast"].shift(1) <= dataframe["slow"].shift(1))
            & (dataframe["close"] > dataframe["regime"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "trend_long")
        dataframe.loc[
            (dataframe["fast"] < dataframe["slow"])
            & (dataframe["fast"].shift(1) >= dataframe["slow"].shift(1))
            & (dataframe["close"] < dataframe["regime"])
            & (dataframe["volume"] > 0),
            ["enter_short", "enter_tag"],
        ] = (1, "trend_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["fast"] < dataframe["slow"], "exit_long"] = 1
        dataframe.loc[dataframe["fast"] > dataframe["slow"], "exit_short"] = 1
        return dataframe


class CryptoBreakout(CryptoTrend):
    """突破过去72小时通道；跌回24小时反向边界退出。"""

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] > dataframe["upper"])
            & (dataframe["close"] > dataframe["regime"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_long")
        dataframe.loc[
            (dataframe["close"] < dataframe["lower"])
            & (dataframe["close"] < dataframe["regime"])
            & (dataframe["volume"] > 0),
            ["enter_short", "enter_tag"],
        ] = (1, "breakout_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] < dataframe["exit_low"], "exit_long"] = 1
        dataframe.loc[dataframe["close"] > dataframe["exit_high"], "exit_short"] = 1
        return dataframe


class CryptoReversion(CryptoTrend):
    """20小时两倍标准差与RSI共同确认的回归，回均线退出。"""

    stoploss = -0.05

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["close"] < dataframe["mean"] - 2 * dataframe["std"])
            & (dataframe["rsi"] < 30)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "reversion_long")
        dataframe.loc[
            (dataframe["close"] > dataframe["mean"] + 2 * dataframe["std"])
            & (dataframe["rsi"] > 70)
            & (dataframe["volume"] > 0),
            ["enter_short", "enter_tag"],
        ] = (1, "reversion_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["close"] >= dataframe["mean"], "exit_long"] = 1
        dataframe.loc[dataframe["close"] <= dataframe["mean"], "exit_short"] = 1
        return dataframe
