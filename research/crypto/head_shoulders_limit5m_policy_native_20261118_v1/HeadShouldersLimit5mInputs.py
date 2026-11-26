"""Frozen source functions plus an official informative merge, never a wallet."""

import hashlib
import importlib.util
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.strategy import merge_informative_pair


HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "head_shoulders_policy_native_20261117_v2"
NATIVE_START = pd.Timestamp("2025-01-01T00:00:00Z")
NATIVE_END = pd.Timestamp("2026-09-01T00:00:00Z")
FIVE = pd.Timedelta(minutes=5)
HOUR = pd.Timedelta(hours=1)
HOURLY_COLUMNS = ["date", "close", "hs_event_id", "hs_direction", "hs_self_terminal"]


def pinned_module(name, filename, digest):
    path = OLD / filename
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Pinned original policy changed")
    if name in sys.modules:
        module = sys.modules[name]
        if Path(module.__file__).resolve() != path:
            raise ValueError("Conflicting source module import")
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OriginalInputs = pinned_module(
    "HeadShouldersInputs",
    "HeadShouldersInputs.py",
    "2fc3e354d49ba56bc941d70fa38eaca2f2cccf288cee84b333562f9cf2ff7132",
)
OriginalStrategy = pinned_module(
    "_fixed_head_shoulders_policy_v2",
    "HeadShouldersStrategy.py",
    "23a84a4d4745cdabde9f7a7309d72a0432b35a1c3c033f9f74b8a1e2feca6542",
).HeadShouldersStrategy


def base_calendar(frame):
    if frame.empty or "date" not in frame:
        raise ValueError("Nonempty native 5m calendar required")
    dates = frame.date
    if not isinstance(dates.dtype, pd.DatetimeTZDtype) or str(dates.dt.tz) != "UTC":
        raise ValueError("Native 5m timestamps must be UTC")
    if dates.isna().any() or not dates.eq(dates.dt.floor("5min")).all():
        raise ValueError("Native 5m timestamps are off grid")
    if not dates.diff().iloc[1:].eq(FIVE).all():
        raise ValueError("Native 5m calendar must be continuous and unique")


def merge_hourly(base, hourly):
    base_calendar(base)
    if any(str(column).endswith("_hs1h") for column in base):
        raise ValueError("Reserved informative suffix collision")
    if not set(HOURLY_COLUMNS).issubset(hourly):
        raise ValueError("Hourly source columns missing")
    if hourly.date.duplicated().any() or not hourly.date.is_monotonic_increasing:
        raise ValueError("Hourly source calendar must be unique and ordered")
    merged = merge_informative_pair(
        base,
        hourly[HOURLY_COLUMNS],
        "5m",
        "1h",
        ffill=True,
        append_timeframe=False,
        suffix="hs1h",
    )
    if not merged.date.equals(base.date.reset_index(drop=True)):
        raise ValueError("Official merge altered the native calendar")
    # The forward-filled informative event must never become repeated entries.
    initial = merged.date.eq(merged.date_hs1h + HOUR - FIVE)
    available = merged.date_hs1h + HOUR
    executable_clock = initial & available.gt(NATIVE_START) & available.lt(NATIVE_END)
    merged["hs_event_id"] = merged.hs_event_id_hs1h.fillna("").where(executable_clock, "")
    merged["hs_direction"] = merged.hs_direction_hs1h.fillna(0).where(executable_clock, 0)
    merged["hs_self_terminal"] = merged.hs_self_terminal_hs1h.fillna(False).astype(bool)
    return merged


def prepared_source(base, pair, config, source, author):
    base_calendar(base)
    if base.date.iloc[0] < NATIVE_START - 800 * FIVE or base.date.iloc[-1] >= NATIVE_END:
        raise ValueError("Native 5m history outside the frozen warmup/window")
    record = config["research_head_shoulders_history"][pair]
    if set(record) != {"path", "sha256"} or not Path(record["path"]).is_absolute():
        raise ValueError("Frozen hourly source history contract differs")
    raw = Path(record["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("Frozen hourly source history changed")
    history = pd.read_feather(io.BytesIO(raw))
    source.validate_frame(history)
    if history.date.iloc[0] != OriginalInputs.ORIGIN:
        raise ValueError("Original source origin differs")
    last_hour = (base.date.iloc[-1] + FIVE).floor("h") - HOUR
    history = history.loc[history.date <= last_hour].copy()
    if history.empty or history.date.iloc[-1] != last_hour:
        raise ValueError("Completed hourly source history missing")
    records = OriginalInputs.event_records(history, pair, source, author)
    projected = OriginalInputs.projected_indicators(history, records)
    return merge_hourly(base, projected), records


def closed_hourly(data, now):
    """Decode ONLY the native 5m analyzed slice; never access a 1h DP cache."""
    if data.empty:
        return pd.DataFrame(columns=["date", "close", "hs_event_id"])
    base_calendar(data)
    required = [f"{name}_hs1h" for name in HOURLY_COLUMNS]
    if not set(required).issubset(data):
        raise ValueError("Merged hourly source columns missing")
    visible = data.loc[data.date + FIVE <= now]
    if visible.empty:
        return pd.DataFrame(columns=["date", "close", "hs_event_id"])
    # Every completed base row must carry the exact most recently closed hour.
    expected = (visible.date + FIVE).dt.floor("h") - HOUR
    if not visible.date_hs1h.eq(expected).all():
        raise ValueError("Merged hourly source is missing, stale or from the future")
    if not (visible.date_hs1h + HOUR <= now).all():
        raise ValueError("Future hourly source escaped availability filter")
    unique = visible[required].drop_duplicates()
    if unique.date_hs1h.duplicated().any():
        raise ValueError("Conflicting duplicate hourly source payload")
    result = unique.rename(columns={f"{name}_hs1h": name for name in HOURLY_COLUMNS})
    if not result.date.diff().iloc[1:].eq(HOUR).all():
        raise ValueError("Completed hourly source sequence has gaps")
    if not np.isfinite(result.close.to_numpy(dtype=float)).all() or result.close.le(0).any():
        raise ValueError("Invalid source close")
    return result.reset_index(drop=True)
