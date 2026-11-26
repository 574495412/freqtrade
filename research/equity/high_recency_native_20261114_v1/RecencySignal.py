"""Local quote-price calendar-recency hypothesis, not source PEAD replication."""

import numpy as np
import pandas as pd

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.strategy import IStrategy


def recency(frame):
    dates = pd.DatetimeIndex(frame.date)
    close = pd.Series(frame.close.to_numpy(), index=dates)
    if dates.has_duplicates or not dates.is_monotonic_increasing or not np.isfinite(close).all():
        raise EquityAdmissionError("Complete finite ordered input required")
    # Explicit local boundary: (t-364 calendar days, t], latest exact tied high.
    age = (
        close.rolling("364D", closed="right")
        .apply(lambda x: np.argmax(x[::-1]), raw=True)
        .astype(int)
        .to_numpy()
    )
    high_dates = dates[np.arange(len(dates)) - age]
    days = (dates - high_dates).days.to_numpy()
    result = pd.Series(1 - days / 364, index=frame.index)
    result.loc[dates < dates[0] + pd.Timedelta(days=364)] = np.nan
    return result


class RecencySignal(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1d"
    startup_candle_count = 0  # Calendar coverage is validated explicitly, not 364 bars.
    can_short = False
    minimal_roi = {}
    stoploss = -0.99

    def bot_start(self, **kwargs):
        if not self.config.get("equity_diagnostic_only", False):
            raise EquityAdmissionError("Stock funded account contract remains unfinished")

    def populate_indicators(self, dataframe, metadata):
        dataframe["calendar_high_recency"] = recency(dataframe)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        return dataframe

    def bot_loop_start(self, **kwargs):
        raise EquityAdmissionError("Diagnostic-only stock strategy; no funded loop")

    def confirm_trade_entry(self, *args, **kwargs):
        return False
