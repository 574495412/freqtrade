"""Local price-only overnight characteristic; never an equity account engine.

Source signal arithmetic: gmaiocc/overnight-strategy-itic at 785e0ab2.
The source uses adjusted OHLC, whereas this explicitly named proxy uses supplied
nominal quotes. Split/dividend units, dissemination and auction fills are not
certified. Callers own a complete venue-session grid and apply native as-of
visibility. Missing observations are rejected, never compressed or zero filled.
"""

import numpy as np
import pandas as pd

from freqtrade.data.history.equity import EquityAdmissionError


def quote_overnight126(panel):
    """Positive source-style 126-session ratio with contemporaneous liquidity.

    All three inputs must already have passed the owning strict equity loader.
    Return full past-only rows for vectorized Registry/IStrategy computation;
    a row is usable only after its scheduled close. USD-only liquidity threshold.
    This is not the existing monthly total-return overnight factor.
    """
    frames = [panel[k] for k in ("open", "close", "volume")]
    base = frames[0]
    if (
        not isinstance(base, pd.DataFrame)
        or not isinstance(base.index, pd.DatetimeIndex)
        or not base.index.is_unique
        or not base.index.is_monotonic_increasing
        or not base.columns.is_unique
        or base.empty
    ):
        raise EquityAdmissionError("Unique ordered complete session and security axes required")
    for frame in frames:
        if not frame.index.equals(base.index) or not frame.columns.equals(base.columns):
            raise EquityAdmissionError("Exact overnight price/volume axes required")
        values = frame.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise EquityAdmissionError("Missing/nonpositive inputs cannot become overnight signals")
    opens, closes, volumes = frames
    ratios = opens / closes.shift(1)
    result = ratios.rolling(126, min_periods=126).apply(np.prod, raw=True) - 1
    liquid = (closes * volumes).rolling(20, min_periods=20).mean() >= 10_000_000
    result = result.where(liquid)
    # Source final signal generator requires at least126+5input rows.
    result.iloc[:130] = np.nan
    if np.isinf(result.to_numpy()).any():
        raise EquityAdmissionError("Overflow in overnight characteristic")
    return result
