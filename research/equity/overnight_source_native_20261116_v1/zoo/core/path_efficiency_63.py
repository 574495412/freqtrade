"""Research candidate: trend path efficiency."""
from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div, zscore

__alpha_meta__ = {
    "id": "core_path_efficiency_63",
    "nickname": "63 日趋势路径效率",
    "theme": ["momentum", "volatility"],
    "formula_latex": r"z\left(\frac{close_t/close_{t-63}-1}{\sum_{j=0}^{62}|r_{t-j}|}\right)",
    "columns_required": ["close"],
    "universe": ["equity_us", "equity_cn", "equity_hk", "crypto"],
    "frequency": ["1d"],
    "decay_horizon": 21,
    "min_warmup_bars": 64,
    "stage": "research",
    "notes": (
        "Signed displacement divided by absolute return path over 63 bars. High values "
        "identify directional movement achieved with less churning, separating smooth "
        "trend from equally large but noisy movement. Fixed window, no tuning and no "
        "future data. It is complementary to raw momentum but may still share price "
        "trend exposure; correlation and incremental-cost tests are required."
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional z-scored signed path efficiency."""
    close = panel["close"].where(panel["close"] > 0)
    returns = close.pct_change(fill_method=None)
    displacement = safe_div(close, close.shift(63)) - 1
    path = returns.abs().rolling(window=63, min_periods=63).sum()
    return zscore(safe_div(displacement, path))
