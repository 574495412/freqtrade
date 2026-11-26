"""Causal source-inspired inputs, never a portfolio account simulator."""

import math

import numpy as np
import pandas as pd


def observations(frame, decision):
    decision = pd.Timestamp(decision)
    # Each daily bar at D is complete at D+1. The return at D uses close[D-1].
    calendar = pd.date_range(decision - pd.Timedelta(days=361), periods=361, freq="1D")
    values = frame.set_index("date").close.reindex(calendar)
    if values.isna().any() or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Missing/invalid complete361-day source price prefix")
    returns = values.to_numpy()[1:] / values.to_numpy()[:-1] - 1
    centered = returns - returns.mean()
    second = float(np.mean(centered**2))
    if second <= 0:
        raise ValueError("Undefined skewness; no zero substitution")
    # Population moment skewness. With common n=360, the usual adjusted-sample
    # multiplier is constant across coins and cannot change cross-sectional ranks.
    skew = float(np.mean(centered**3) / second**1.5)
    volatility = float(np.std(returns[-63:], ddof=1))
    if not math.isfinite(skew) or not math.isfinite(volatility) or volatility <= 0:
        raise ValueError("Undefined source feature")
    return {
        "skew360": skew,
        "volatility63": volatility,
        "price_rows": 361,
        "last_complete_bar": str(calendar[-1]),
        "decision_at": str(decision),
    }


def monthly_targets(rows, field):
    if len(rows) != 40 or len({r["pair"] for r in rows}) != 40:
        raise ValueError("Keep the entire fixed40 support; do not delete missing names")
    ordered = sorted(rows, key=lambda row: (row[field], row["pair"]))
    return {row["pair"]: (1 if i < 10 else -1 if i >= 30 else 0) for i, row in enumerate(ordered)}
