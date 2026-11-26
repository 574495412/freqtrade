"""Minimal correct usage of Freqtrade's open interest candle type.

This is a REFERENCE, not a strategy to trade. It shows the API and the two
traps, and deliberately contains no edge: the entry condition here is the
shape the measurements in the README support (open interest confirming a move
that has already started), with no tuned thresholds.

Written against the interface merged in freqtrade#13503 on 2026-08-30.

Download the data first -- open interest is not part of the default download:

    freqtrade download-data --exchange bybit --pairs BTC/USDT:USDT \
      --timeframes 1h --trading-mode futures --candle-types open_interest
"""
from pandas import DataFrame

from freqtrade.strategy import IStrategy, informative


class OpenInterestReference(IStrategy):
    timeframe = "1h"
    can_short = True
    startup_candle_count = 60

    minimal_roi = {"0": 100}          # deliberately inert
    stoploss = -0.10

    @informative("1h", candle_type="open_interest")
    def populate_indicators_oi_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Open interest arrives with ONLY the two open-interest columns.

        No open/high/low/close/volume here -- that is expected, not a bug.

        TRAP 1: use `open_interest_amount`, the base-currency contract count.
        `open_interest_value` is quote-denominated, which means it moves with
        price; a signal built on it is partly a price signal in disguise. On
        Bybit linear it is all-NaN anyway.
        """
        oi = dataframe["open_interest_amount"]

        # Relative change over a short lookback. Relative, because absolute
        # contract counts are not comparable across instruments.
        dataframe["oi_change_pct"] = oi.pct_change(periods=5) * 100

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # The informative decorator merges the frame above in with a suffix.
        # Guard the column: a strategy that silently sees NaN is worse than one
        # that fails loudly.
        if "oi_change_pct_1h" not in dataframe:
            raise ValueError(
                "open interest columns are missing -- download them explicitly "
                "with --candle-types open_interest"
            )

        dataframe["hh_20"] = dataframe["high"].rolling(20).max().shift(1)
        dataframe["ll_20"] = dataframe["low"].rolling(20).min().shift(1)
        # .shift(1) is load-bearing: without it the channel includes the bar
        # being evaluated, and the breakout can never be detected -- or worse,
        # is detected using the bar's own high. That is lookahead.

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Open interest as CONFIRMATION, never as trigger.

        The measurement behind this shape: rising open interest while price is
        range-bound scores 0.47x the base rate -- it loses. The same increase
        while price is already moving scores up to 7.34x. So price leads and
        open interest confirms; the reverse does not work.

        No threshold is tuned here. `> 0` is the weakest possible form of the
        condition on purpose -- picking a number without measuring it on your
        own signals is how you end up with a filter that cuts your profitable
        half. Measure it, paired: kept trades against rejected trades on the
        SAME signal set.
        """
        breakout_up = dataframe["close"] > dataframe["hh_20"]
        breakout_dn = dataframe["close"] < dataframe["ll_20"]
        oi_rising = dataframe["oi_change_pct_1h"] > 0

        dataframe.loc[breakout_up & oi_rising, "enter_long"] = 1
        dataframe.loc[breakout_dn & oi_rising, "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exits left to the stoploss on purpose. The exit path is where money
        # actually leaks, and it deserves its own measurement rather than a
        # placeholder that looks like a decision.
        return dataframe
