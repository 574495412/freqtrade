"""Local source-conditioned crypto residual feature; not an account engine.

The source's locked OOS notebook owns the 720/684-hour beta and 336/319-hour
formation windows. This adapter requires observed joint hourly closes, derives
simple returns locally, and retains the source's beta-only subtraction (no
intercept removal, residual volatility normalization or future fitting).
"""

import numpy as np
import pandas as pd


def source_residual_momentum(close: pd.DataFrame) -> dict[str, pd.DataFrame | pd.Series]:
    if not isinstance(close.index, pd.DatetimeIndex) or close.index.tz is None:
        raise ValueError("Timezone-aware native hourly close observations required")
    if close.empty or close.columns.has_duplicates or len(close.columns) != 8:
        raise ValueError("Exactly eight distinct source-universe columns required")
    expected = pd.date_range(close.index[0], close.index[-1], freq="h")
    if not close.index.equals(expected) or any(
        t.minute or t.second or t.microsecond for t in close.index
    ):
        raise ValueError("Complete increasing hourly observation grid required")
    values = close.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Observed finite positive native closes required; no price fill")
    returns = close.pct_change(fill_method=None)
    market = returns.mean(axis=1, skipna=False)
    variance = market.rolling(720, min_periods=684).var()
    beta = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for pair in close.columns:
        beta[pair] = returns[pair].rolling(720, min_periods=684).cov(market) / variance
    beta = beta.replace([np.inf, -np.inf], np.nan)
    residual = returns - beta.mul(market, axis=0)
    signal = residual.rolling(336, min_periods=319).sum()
    # Same-horizon raw-momentum control; not another accepted alpha.
    raw = returns.rolling(336, min_periods=319).sum()
    return {
        "returns": returns,
        "market": market,
        "beta": beta,
        "residual": residual,
        "signal": signal,
        "raw": raw,
    }
