"""Local full-window price-path inputs; no admitted strategy or stock replication.

PyTimeTK 246299cdd1d76b454cbccd0a614978eeb3cfbbae defines original
FIP=PRET*(p_neg-p_pos) and modified FIP=sign(PRET)*(p_pos-p_neg).
Both measure path strength without trade direction. The signed hypothesis
PRET*modified=abs(PRET)*(p_pos-p_neg) is explicitly a local crypto extension.
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


def daily_fip_components(close):
    """Unshifted values belong to a completed bar, never its opening instant."""
    if not isinstance(close, pd.DataFrame) or not close.columns.is_unique or not len(close.columns):
        raise ValueError("Unique explicit security columns required")
    _grid(close.index, "1D")
    if not close.index.equals(close.index.normalize()):
        raise ValueError("Exact daily UTC midnight bar labels required")
    values = close.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Positive finite observed prices required; no filling")
    returns = close.pct_change(fill_method=None)
    result = {}
    for window in (63, 252):
        count = returns.notna().rolling(window, min_periods=window).sum()
        positive = (returns > 0).rolling(window, min_periods=window).sum()
        negative = (returns < 0).rolling(window, min_periods=window).sum()
        balance = ((positive - negative) / window).where(count.eq(window))
        pret = returns.rolling(window, min_periods=window).apply(
            lambda x: np.prod(1 + x) - 1, raw=True
        )
        modified = np.sign(pret) * balance
        for name, frame in {
            "pret": pret, "sign_balance": balance, "source_original": -pret * balance,
            "source_modified": modified, "id": -modified, "signed_path": pret * modified,
        }.items():
            result[name + str(window)] = frame
    result["lowvol63"] = -returns.rolling(63, min_periods=63).std(ddof=1)
    return result


def hourly_completed_fip_view(parts, hours):
    """At hour H, select only the daily bar fully closed by floor_day(H)."""
    _grid(hours, "1h")
    if not hours.equals(hours.floor("h")):
        raise ValueError("Exact hourly bar labels required")
    expected = {name + str(w) for w in (63, 252) for name in (
        "pret", "sign_balance", "source_original", "source_modified", "id", "signed_path"
    )} | {"lowvol63"}
    if set(parts) != expected:
        raise ValueError("Exact declared FIP/control components required")
    previous = hours.normalize() - pd.Timedelta(days=1)
    reference = parts["pret63"]
    result = {}
    for name, frame in parts.items():
        if not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns):
            raise ValueError("All components must share observation and security axes")
        result[name] = pd.DataFrame(
            frame.reindex(previous).to_numpy(), index=hours, columns=frame.columns
        )
    return result
