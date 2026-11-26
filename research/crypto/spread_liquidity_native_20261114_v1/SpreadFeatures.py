"""Causal raw features called inside the native strategy; no account engine."""

import numpy as np
import pandas as pd
from edge_author import edge


def calculate_features(daily, quote_volume):
    frame = daily.sort_values("date").copy().reset_index(drop=True)
    if frame.date.duplicated().any():
        raise ValueError("Duplicate daily input")
    valid = np.isfinite(frame[["open", "high", "low", "close"]]).all(axis=1)
    valid &= frame[["open", "high", "low", "close"]].gt(0).all(axis=1)
    valid &= frame.high.ge(frame[["open", "close", "low"]].max(axis=1))
    valid &= frame.low.le(frame[["open", "close", "high"]].min(axis=1))
    raw_returns = frame.close / frame.close.shift(1) - 1
    dollars = quote_volume.reindex(pd.DatetimeIndex(frame.date)).to_numpy()
    illiq = raw_returns.abs() / dollars
    illiq = illiq.where(np.isfinite(dollars) & (dollars > 0))
    scores = pd.DataFrame({"decision_date": frame.date + pd.Timedelta(days=1)})
    scores["edge30"] = np.nan
    scores["amihud30"] = illiq.rolling(30, min_periods=30).mean()
    scores["lowvol63"] = -raw_returns.rolling(63, min_periods=63).std(ddof=1)
    for i in range(29, len(frame)):
        window = frame.iloc[i - 29 : i + 1]
        if not valid.iloc[i - 29 : i + 1].all() or window.date.iloc[-1] - window.date.iloc[
            0
        ] != pd.Timedelta(days=29):
            continue
        scores.loc[i, "edge30"] = edge(
            *(window[c].to_numpy() for c in ["open", "high", "low", "close"]), sign=False
        )
    support30 = valid.rolling(31, min_periods=31).sum().eq(31)
    support30 &= frame.date.sub(frame.date.shift(30)).eq(pd.Timedelta(days=30))
    support63 = valid.rolling(64, min_periods=64).sum().eq(64)
    support63 &= frame.date.sub(frame.date.shift(63)).eq(pd.Timedelta(days=63))
    scores.loc[~support30, "amihud30"] = np.nan
    scores.loc[~support63, "lowvol63"] = np.nan
    return scores


def targets(features, field, pairs):
    # Cross-sectional ranking occurs in this native strategy helper. Missing
    # observations yield an explicit cash target, not an imputed factor value.
    joined = pd.concat({p: features[p].set_index("decision_date")[field] for p in pairs}, axis=1)
    result = {}
    for date, row in joined.iterrows():
        chosen = {p: 0 for p in pairs}
        if np.isfinite(row).all():
            ordered = sorted(pairs, key=lambda p: (float(row[p]), p))
            chosen[ordered[0]], chosen[ordered[-1]] = -1, 1
        result[date.strftime("%Y-%m-%d")] = chosen
    return result
