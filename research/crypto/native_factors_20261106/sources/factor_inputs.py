"""Frozen factor math under an explicit bar-close timing assumption.

No receipt times, orderbooks, signals, exchange calls or accounting are invented.
The original receipt-strict implementation remains untouched.
"""
import math

import numpy as np
import pandas as pd


def daily_features(hourly: pd.DataFrame, decisions: pd.DatetimeIndex) -> pd.DataFrame:
    if decisions.tz is None:
        raise ValueError("UTC midnight decisions required")
    decisions = decisions.tz_convert("UTC")
    if not (decisions == decisions.normalize()).all():
        raise ValueError("UTC midnight decisions required")
    out = pd.DataFrame(index=decisions, columns=["momentum168", "scale24"], dtype=float)
    if hourly.empty:
        return out
    dates = pd.DatetimeIndex(hourly["date"])
    if dates.tz is None:
        raise ValueError("Timezone-aware hour opens required")
    dates = dates.tz_convert("UTC")
    if dates.has_duplicates or not (dates == dates.floor("h")).all():
        raise ValueError("Unique exact-hour bars required")
    closes = pd.Series(pd.to_numeric(hourly["close"], errors="coerce").to_numpy(), index=dates).sort_index()
    closes = closes.reindex(pd.date_range(closes.index.min(), closes.index.max(), freq="h"))
    closes = closes.where(np.isfinite(closes) & (closes > 0))
    logclose = np.log(closes)
    momentum = (logclose - logclose.shift(168)).where(closes.rolling(169).count() == 169)
    sigma = logclose.diff().rolling(24, min_periods=24).std(ddof=1) * math.sqrt(24 * 365)
    scale = (0.20 / sigma).clip(upper=1).where(np.isfinite(sigma) & (sigma > 0))
    # A row labelled 23:00 contributes at 00:00, not at 23:00.
    feature = pd.DataFrame({"momentum168": momentum, "scale24": scale})
    feature.index = feature.index + pd.Timedelta(hours=1)
    return feature.reindex(decisions)


def cross_section(frames, universe, decisions):
    if not universe or len(set(universe)) != len(universe):
        raise ValueError("Nonempty unique frozen universe required")
    values = pd.DataFrame(index=decisions, columns=universe, dtype=float)
    for pair in universe:
        if pair in frames:
            values[pair] = daily_features(frames[pair], decisions)["momentum168"]
    valid = values.notna().all(axis=1)
    # Stable tie order is the frozen universe order, never alphabetical/input order.
    ranks = values.rank(axis=1, method="first", ascending=False)
    selected = ranks.le(3) & values.gt(0)
    selected.loc[~valid, :] = False
    return values, selected, valid


def effective_daily_scale(hourly, current_time):
    now = pd.Timestamp(current_time)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware execution time required")
    now = now.tz_convert("UTC")
    day = now.normalize()
    # At 00:00 the new decision has not reached its 00:05 execution boundary.
    if now < day + pd.Timedelta(minutes=5):
        day -= pd.Timedelta(days=1)
    return daily_features(hourly, pd.DatetimeIndex([day])).iloc[0]["scale24"]


def scale_risk_approved_stake(base_stake, scale, min_stake, max_stake):
    """Only size arithmetic; base_stake must come from the shared RD1 adapter."""
    if not all(math.isfinite(x) for x in (base_stake, scale, max_stake)):
        return 0.0
    if base_stake <= 0 or not 0 < scale <= 1 or max_stake <= 0:
        return 0.0
    stake = min(base_stake * scale, max_stake)
    # Return zero before Freqtrade's minimum-stake clamp could enlarge the risk.
    return 0.0 if min_stake is not None and stake < min_stake else stake
