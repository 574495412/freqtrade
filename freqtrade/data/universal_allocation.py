"""Local price-only Cover allocation signal, never a funded account simulator.

Cover (1991), equations 1.3--1.5: average fixed portfolios under their past
relative wealth. Equal mass on a finite triangular lattice approximates the
uniform simplex integral. No asymptotic or transaction-cost guarantee transfers
to this finite approximation or to the native execution that consumes it.
"""

import numpy as np
import pandas as pd


def triangular_portfolios(resolution=100):
    if type(resolution) is not int or not 1 <= resolution <= 1000:
        raise ValueError("Explicit positive bounded integer simplex resolution required")
    return (
        np.array(
            [
                (i, j, resolution - i - j)
                for i in range(resolution + 1)
                for j in range(resolution + 1 - i)
            ],
            dtype=float,
        )
        / resolution
    )


def cover_allocation_weights(prices, decisions, *, resolution=100):
    """Return day-D weights using only closes strictly before day D.

    ``prices`` contains complete UTC daily labels for two ordered risky assets.
    A price labelled D-1 is available at D00:00. First decision starts uniformly
    without pre-window training; later decisions consume one completed daily
    relative. Cash is explicit, unremunerated and not a tradeable third pair.
    Extra future rows are ignored; missing relevant rows are never filled.
    """
    if not isinstance(prices, pd.DataFrame) or list(prices.columns) != ["BTC/USDT", "ETH/USDT"]:
        raise ValueError("Ordered BTC/ETH price-only input required")
    dates = pd.DatetimeIndex(decisions)
    if (
        dates.empty
        or dates.tz is None
        or str(dates.tz) != "UTC"
        or not dates.equals(dates.normalize())
        or not dates.equals(pd.date_range(dates[0], dates[-1], freq="D"))
    ):
        raise ValueError("Complete consecutive UTC decision days required")
    if (
        not isinstance(prices.index, pd.DatetimeIndex)
        or prices.index.tz is None
        or str(prices.index.tz) != "UTC"
        or not prices.index.is_unique
        or not prices.index.is_monotonic_increasing
    ):
        raise ValueError("Unique ordered UTC daily price labels required")
    needed = pd.date_range(
        dates[0] - pd.Timedelta(days=1), dates[-1] - pd.Timedelta(days=1), freq="D"
    )
    selected = prices.reindex(needed)
    values = selected.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Complete positive observed prices required;no imputation")
    portfolios = triangular_portfolios(resolution)
    posterior = np.full(len(portfolios), 1.0 / len(portfolios))
    weights = [np.full(3, 1.0 / 3)]
    for row in range(1, len(dates)):
        relative = np.r_[values[row] / values[row - 1], 1.0]
        posterior *= portfolios @ relative
        posterior /= posterior.sum()
        weights.append(posterior @ portfolios)
    result = pd.DataFrame(weights, index=dates, columns=[*prices.columns, "CASH"])
    if (
        not np.isfinite(result).all().all()
        or (result < 0).any().any()
        or not np.allclose(result.sum(axis=1), 1.0, atol=1e-12, rtol=0)
    ):
        raise ValueError("Finite long-only unit-simplex allocation required")
    return result
