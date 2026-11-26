"""Local source-faithful ordinal entropy feature, not an upstream trading strategy.

Reference: neurotrader888/PermutationEntropy 890da371, MIT. Native wallets and
fills remain the responsibility of Freqtrade; this module only builds features.
"""

import math

import numpy as np
import pandas as pd


def ordinal_patterns(values, dimension=3):
    """Preserve the source's >= tie handling and factorial ordinal encoding."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all() or dimension != 3:
        raise ValueError("Finite one-dimensional observations and frozen dimension3 required")
    result = np.full(len(values), np.nan)
    if len(values) < dimension:
        return result
    encoded = np.zeros(len(values) - dimension + 1)
    for lag in range(1, dimension):
        multiplier = math.factorial(dimension) / math.factorial(lag + 1)
        for recent in range(lag):
            older = values[dimension - 1 - lag : len(values) - lag]
            newer = values[dimension - 1 - recent : len(values) - recent]
            encoded += (older >= newer) * multiplier
    result[dimension - 1 :] = encoded
    return result


def permutation_entropy(values, dimension=3, multiplier=28):
    """Source demo's168-pattern window and first-valid index170, unchanged."""
    if dimension != 3 or multiplier != 28:
        raise ValueError("Only the preregistered source demo dimension3/multiplier28 supported")
    ordinals = ordinal_patterns(values, dimension)
    lookback = math.factorial(dimension) * multiplier
    total = np.zeros(len(ordinals))
    for pattern in range(math.factorial(dimension)):
        frequency = pd.Series(ordinals == pattern).rolling(lookback).sum() / lookback
        positive = frequency.where(frequency > 0)
        total += (positive * np.log2(positive)).fillna(0).to_numpy()
    result = -total / math.log2(math.factorial(dimension))
    result[: lookback + dimension - 1] = np.nan
    return result


def entropy_gate(hourly):
    """Gate at hourly CLOSE availability using only the preceding8760 entropies.

    This median gate is a new local hypothesis, not a source trading rule.
    Input columns and values match the sealed BTC/ETH signal-price contract.
    """
    if list(hourly.columns) != ["BTC/USDT", "ETH/USDT"] or hourly.empty:
        raise ValueError("Fixed BTC/ETH hourly close input required")
    ix = hourly.index
    if not isinstance(ix, pd.DatetimeIndex) or str(ix.tz) != "UTC":
        raise ValueError("UTC candle OPEN index required")
    if not ix.as_unit("ns").equals(pd.date_range(ix[0], ix[-1], freq="h").as_unit("ns")):
        raise ValueError("Complete increasing contiguous hourly input required")
    if not np.isfinite(hourly.to_numpy()).all() or not hourly.gt(0).all().all():
        raise ValueError("Finite positive closes required; no imputation")
    entropy = pd.DataFrame(
        {pair: permutation_entropy(hourly[pair].to_numpy()) for pair in hourly},
        index=ix + pd.Timedelta(hours=1),
    )
    threshold = entropy.shift(1).rolling(8760, min_periods=8760).median()
    gate = entropy.le(threshold) & threshold.notna() & entropy.notna()
    return entropy, threshold, gate
