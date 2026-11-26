"""Native strategy input wrapper around the source TA-Lib 0.8.1 indicator."""
import numpy as np
import pandas as pd
import talib


def phase_features(frame):
    clocks = pd.DatetimeIndex(frame.date)
    close = frame.close.to_numpy(dtype=float, copy=True)
    if (clocks.tz is None or clocks.hasnans or clocks.has_duplicates
            or not clocks.is_monotonic_increasing or not len(close)
            or not np.isfinite(close).all() or not (close > 0).all()):
        raise ValueError('Ordered unique aware clocks and finite positive closes required')
    if (not talib.__ta_version__.startswith(b'0.8.1 ') or talib.get_compatibility() != 0
            or talib.get_unstable_period('MAMA') != 0):
        raise ValueError('Frozen native indicator environment required')
    result = frame.copy()
    result['mama'], result['fama'] = talib.MAMA(close, fastlimit=.5, slowlimit=.05)
    return result
