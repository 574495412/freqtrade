"""Local past-only MAX28 input extension, not an admitted trading strategy.

Ozdamar/Akdeniz/Sensoy (2021), doi:10.1186/s40854-021-00291-9,
Eq1 and footnote9: maximum daily return over28days, observed weekly.
The close-to-close simple return is the explicit local quote convention.
"""

import numpy as np
import pandas as pd


def _grid(index, frequency):
    if (
        not isinstance(index, pd.DatetimeIndex)
        or str(index.tz) != "UTC"
        or not len(index)
        or not index.is_unique
        or not index.is_monotonic_increasing
        or not index.equals(pd.date_range(index[0], index[-1], freq=frequency))
    ):
        raise ValueError("Complete ordered UTC observation grid required")


def daily_max_components(close):
    """Unshifted values belong to the completed daily bar, not its opening."""
    if not isinstance(close, pd.DataFrame) or not close.columns.is_unique or not len(close.columns):
        raise ValueError("Unique explicit security columns required")
    _grid(close.index, "1D")
    if not close.index.equals(close.index.normalize()):
        raise ValueError("Daily UTC midnight bar labels required")
    values = close.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Observed positive finite closing prices required; no filling")
    returns = close.pct_change(fill_method=None)
    return {
        "max28": returns.rolling(28, min_periods=28).max(),
        "mom21_skip7": close.shift(7) / close.shift(28) - 1,
        "lowvol28": -returns.rolling(28, min_periods=28).std(ddof=1),
        "raw28": close / close.shift(28) - 1,
        # Sample-skewness is a scoped overlap diagnostic, not another candidate
        # or the older annual population-skewness strategy.
        "skew28": returns.rolling(28, min_periods=28).skew(),
    }


def hourly_completed_daily_view(parts, hours):
    """Hour H sees only the UTC daily bar that closed by floor_day(H)."""
    _grid(hours, "1h")
    if not hours.equals(hours.floor("h")):
        raise ValueError("Exact hourly bar labels required")
    if set(parts) != {"max28", "mom21_skip7", "lowvol28", "raw28", "skew28"}:
        raise ValueError("Exact declared MAX/control components required")
    previous = hours.normalize() - pd.Timedelta(days=1)
    result = {}
    reference = parts["max28"]
    for name, frame in parts.items():
        if not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns):
            raise ValueError("Components must share exact observations and ordered securities")
        result[name] = pd.DataFrame(
            frame.reindex(previous).to_numpy(), index=hours, columns=frame.columns
        )
    return result
