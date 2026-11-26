"""Local daily external-metric clock contract, not upstream network-data support.

Completion times describe calculations; they do not certify when this particular
version was first published. Snapshot-observed mode is therefore the default.
The explicit completion-clock model is for isolated retrospective research.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

import numpy as np
import pandas as pd


class OnchainInputError(ValueError):
    """A declared metric/time contract cannot be satisfied."""


class MetricClock(StrEnum):
    SNAPSHOT_OBSERVED = "snapshot_observed"
    COMPLETION_MODEL = "completion_model"


def _completion(value):
    if value is None or str(value) in {"", "nan", "None"}:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise OnchainInputError("Invalid supplied completion clock") from error
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise OnchainInputError("Invalid supplied completion clock")
    return int(number)


def _hash_value(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise OnchainInputError("Invalid supplied hash rate") from error
    if not number.is_finite() or number <= 0:
        raise OnchainInputError("Unknown/nonpositive current metric; no value fallback")
    return number


def reconcile_hash_completion_snapshots(current, archived):
    """Same-value calculation-clock evidence; never a historical-publication claim.

    Original current nulls and both source clocks remain in the output. A clock
    is usable only when it is actually supplied alongside the exact same raw
    HashRate value. Conflicting versions fail instead of choosing one for alpha.
    """
    columns = {"date", "HashRate", "AssetEODCompletionTime"}
    if not columns.issubset(current) or not columns.issubset(archived):
        raise OnchainInputError("Both source snapshots require exact fields")
    a, b = current.copy(), archived.copy()
    for frame in (a, b):
        frame["date"] = pd.to_datetime(frame.date, utc=True)
        if frame.date.duplicated().any():
            raise OnchainInputError("Duplicate source snapshot date")
    older = b.set_index("date")
    rows = []
    for row in a.to_dict("records"):
        value = _hash_value(row["HashRate"])
        previous = None
        if row["date"] in older.index:
            item = older.loc[row["date"]]
            if str(item.HashRate) not in {"", "nan", "None"}:
                if _hash_value(item.HashRate) != value:
                    raise OnchainInputError("Conflicting HashRate snapshot values")
                previous = _completion(item.AssetEODCompletionTime)
        latest = _completion(row["AssetEODCompletionTime"])
        known = [x for x in (latest, previous) if x is not None]
        rows.append(
            {
                "date": row["date"],
                "HashRate": float(value),
                "hash_source_decimal": str(value),
                "completion_current": latest,
                "completion_archived": previous,
                "AssetEODCompletionTime": max(known) if known else np.nan,
                "current_completion_missing": latest is None,
                "clock_evidence": "same-value supplied timestamps, conservative maximum",
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class DailyHashSource:
    """One immutable metric snapshot and its actually observed receipt time."""

    frame: pd.DataFrame
    observed_at: datetime

    def __post_init__(self):
        stamp = pd.Timestamp(self.observed_at)
        if stamp.tzinfo is None:
            raise OnchainInputError("Timezone-aware snapshot receipt time required")
        frame = self.frame.copy(deep=True)
        required = {"date", "HashRate", "AssetEODCompletionTime"}
        if not required.issubset(frame.columns) or frame.empty:
            raise OnchainInputError("Exact daily hash rate and completion fields required")
        dates = pd.DatetimeIndex(pd.to_datetime(frame.date, utc=True)).as_unit("ns")
        if not dates.equals(pd.date_range(dates[0], dates[-1], freq="D")):
            raise OnchainInputError("Unique complete daily metric grid required; no gap fill")
        if not dates.equals(dates.normalize()):
            raise OnchainInputError("UTC midnight start-of-interval labels required")
        values = frame[["HashRate", "AssetEODCompletionTime"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise OnchainInputError("Unknown or nonpositive metric/completion value")
        seconds = values[:, 1]
        if (seconds != np.floor(seconds)).any() or (seconds >= 10**11).any():
            raise OnchainInputError("Completion timestamps must be integer epoch seconds")
        completed = pd.DatetimeIndex(pd.to_datetime(seconds, unit="s", utc=True)).as_unit("ns")
        if (completed < dates + pd.Timedelta(days=1)).any():
            raise OnchainInputError("Completion clock precedes the metric interval end")
        frame["date"] = dates
        frame["completed_at"] = completed
        object.__setattr__(self, "frame", frame)
        object.__setattr__(self, "observed_at", stamp.tz_convert("UTC"))

    def features(self, clock=MetricClock.SNAPSHOT_OBSERVED, delay_hours=0):
        """Every 60-day input must be complete before its feature is available."""
        clock = MetricClock(clock)
        if not isinstance(delay_hours, int) or delay_hours < 0:
            raise OnchainInputError("Nonnegative whole-hour publication delay required")
        f = self.frame
        result = pd.DataFrame({"metric_day": f.date})
        result["hash_sma30"] = f.HashRate.rolling(30, min_periods=30).mean()
        result["hash_sma60"] = f.HashRate.rolling(60, min_periods=60).mean()
        result["hash_ratio"] = result.hash_sma30 / result.hash_sma60 - 1
        seconds = f.AssetEODCompletionTime.rolling(60, min_periods=60).max()
        available = pd.to_datetime(seconds, unit="s", utc=True).astype("datetime64[ns, UTC]")
        if clock == MetricClock.SNAPSHOT_OBSERVED:
            available = available.where(available >= self.observed_at, self.observed_at)
            available = available.where(seconds.notna())
        result["available_at"] = available + pd.Timedelta(hours=delay_hours)
        return result.dropna().reset_index(drop=True)

    def asof_hours(
        self, hours, *, clock=MetricClock.SNAPSHOT_OBSERVED, delay_hours=0, max_age_hours=72
    ):
        """Latest eligible observation, never an event-date join or a future value."""
        hours = pd.DatetimeIndex(hours)
        if hours.tz is None or hours.has_duplicates or not hours.is_monotonic_increasing:
            raise OnchainInputError("Ordered unique timezone-aware decision clocks required")
        hours = hours.tz_convert("UTC").as_unit("ns")
        if max_age_hours <= 0:
            raise OnchainInputError("Explicit positive maximum metric age required")
        events = self.features(clock, delay_hours).sort_values(["available_at", "metric_day"])
        # An older observation arriving later must never rewind the active day.
        newest = events.metric_day.cummax()
        events = events.loc[events.metric_day.eq(newest)].drop_duplicates(
            "available_at", keep="last"
        )
        result = pd.merge_asof(
            pd.DataFrame({"clock": hours}),
            events,
            left_on="clock",
            right_on="available_at",
            direction="backward",
        )
        age = result.clock - (result.metric_day + pd.Timedelta(days=1))
        result["age_hours"] = age.dt.total_seconds() / 3600
        result["status"] = np.where(result.metric_day.isna(), "UNAVAILABLE", "KNOWN")
        result.loc[result.age_hours.gt(max_age_hours), "status"] = "STALE"
        result["snapshot_observed_before_decision"] = result.clock.ge(
            self.observed_at
        ) & result.status.eq("KNOWN")
        return result


def require_complete_metric_clock(frame):
    """Unknown/stale observations cannot become cash, zero or an old fallback."""
    bad = frame.status.ne("KNOWN")
    if bad.any():
        raise OnchainInputError(f"Unadmitted metric clock at {int(bad.sum())} decision hours")
