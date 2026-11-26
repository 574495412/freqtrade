"""Local UTC calendar timing signal, not a predictive factor/account engine.

The two published defaults are fixed in the study. Calendar dates are known
before a trading decision; no price, return, future quote or optimized date is
an input. Native strategy conversion still supplies the execution-bar lag.
"""

import pandas as pd


def month_boundary_state(clocks, *, last, first):
    if (
        type(last) is not int
        or type(first) is not int
        or last < 0
        or first < 0
        or last > 7
        or first > 7
        or not 0 < last + first <= 14
    ):
        raise ValueError("Explicit bounded calendar-day counts required")
    times = pd.DatetimeIndex(clocks)
    if (
        times.empty
        or times.tz is None
        or str(times.tz) != "UTC"
        or times.hasnans
        or not times.is_unique
        or not times.is_monotonic_increasing
    ):
        raise ValueError("Unique ordered aware UTC calendar clocks required")
    values = (times.day <= first) | ((times.days_in_month - times.day) < last)
    return pd.Series(values, index=times, name="month_boundary", dtype=bool)
