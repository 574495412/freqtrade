"""Local historical positioning input at native IStrategy's supported boundary.

No cash, fills or returns are produced. Archives are supplied with checked
receipts; this adapter never queries live endpoints or invents missing metrics.
"""

from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


RATIO_COLUMNS = (
    "count_long_short_ratio",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def daily_positioning(records, symbol):
    """Exact23:55 observation on a complete declared archive-day calendar.

    Input ownership verifies archive and official checksum bytes separately.
    Source missing/nonpositive ratios remain NaN; an absent exact snapshot is
    not replaced by a nearby observation. All unrelated raw rows stay in ZIPs.
    """
    rows, diagnostics = [], []
    selected = sorted((r for r in records if r["symbol"] == symbol), key=lambda r: r["day"])
    if not selected:
        raise ValueError("No historical positioning archives for exact symbol")
    labels = [r["day"] for r in selected]
    if len(labels) != len(set(labels)):
        raise ValueError("Duplicate positioning archive date")
    dates = pd.DatetimeIndex(pd.to_datetime(labels, utc=True))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq="D")):
        raise ValueError("Missing positioning archive day; no compressed calendar")
    for record, day in zip(selected, dates, strict=True):
        with ZipFile(Path(record["path"])) as archive:
            names = archive.namelist()
            if len(names) != 1 or not names[0].endswith(".csv"):
                raise ValueError("One metrics CSV per official archive required")
            frame = pd.read_csv(archive.open(names[0]))
        needed = ["create_time", "symbol", *RATIO_COLUMNS]
        if not set(needed).issubset(frame):
            raise ValueError("Missing raw positioning schema")
        clock = pd.DatetimeIndex(pd.to_datetime(frame.create_time, utc=True))
        if clock.has_duplicates or not frame.symbol.eq(symbol).all():
            raise ValueError("Ambiguous duplicate or foreign-symbol positioning rows")
        if not clock.normalize().equals(pd.DatetimeIndex([day] * len(clock))):
            raise ValueError("Metrics row belongs to another UTC archive day")
        if not clock.minute.isin(range(0, 60, 5)).all() or clock.second.any():
            raise ValueError("Metrics observation outside exact five-minute grid")
        exact = day + pd.Timedelta(hours=23, minutes=55)
        pick = np.flatnonzero(clock == exact)
        values = np.full(len(RATIO_COLUMNS), np.nan)
        if len(pick) == 1:
            values = frame.iloc[pick[0]][list(RATIO_COLUMNS)].to_numpy(dtype=float)
            values[~np.isfinite(values) | (values <= 0)] = np.nan
        rows.append(values)
        diagnostics.append({
            "symbol": symbol, "day": day.isoformat(), "raw_rows": len(frame),
            "raw_rows_were_sorted": clock.is_monotonic_increasing,
            "exact_snapshot_present": len(pick) == 1,
            "invalid_exact_fields": [c for c, v in zip(RATIO_COLUMNS, values, strict=True)
                                     if not np.isfinite(v)],
        })
    result = pd.DataFrame(rows, columns=RATIO_COLUMNS, index=dates)
    result.index.name = "observation_day"
    return result, diagnostics


def trailing_midrank(series, window=365):
    """Declared local contract:365previous calendar days, current excluded."""
    if window != 365 or not series.index.equals(
        pd.date_range(series.index[0], series.index[-1], freq="D")
    ):
        raise ValueError("Frozen365day daily positioning rank contract required")
    x = series.to_numpy(dtype=float)
    result = np.full(len(x), np.nan)
    for i in range(window, len(x)):
        past = x[i-window:i]
        if np.isfinite(x[i]) and np.isfinite(past).all():
            result[i] = (np.count_nonzero(past < x[i]) + .5*np.count_nonzero(past == x[i]))/window
    return pd.Series(result, index=series.index, name="trailing_midrank")


def monthly_opportunities(rank, account_start, account_end):
    """Only targets:30days per valid event, union without pyramiding.

    The source is a gross event study, not a funded allocation policy. This
    local policy is frozen before any account outcome. Unknown observations do
    not open new opportunities; already-known opportunities retain their expiry.
    """
    decisions = pd.date_range(account_start, account_end, tz="UTC", freq="D")
    observed = rank.reindex(decisions - pd.Timedelta(days=1))
    known = np.isfinite(observed.to_numpy(dtype=float))
    events = known & (observed.to_numpy(dtype=float) <= .10)
    active = np.array([events[max(0, i-29):i+1].any() for i in range(len(events))])
    return pd.DataFrame({"rank": observed.to_numpy(), "known": known,
                         "new_opportunity": events, "active": active}, index=decisions)
