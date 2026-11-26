"""Local observed-minute-VWAP input contract for realized signed variation.

No trades, positions, portfolio returns or future labels are computed here.
The source CIRF2022 draft's daily RSJ/RSK/RKT formulas use five-minute log
returns, not centered moments of daily returns or a jump threshold test.
"""

import numpy as np
import pandas as pd


def daily_signed_variation(observations: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "available_at", "vwap", "base_volume", "quote_volume"}
    if not required <= set(observations):
        raise ValueError("Explicit observed-minute-VWAP schema required")
    index = pd.DatetimeIndex(observations.date)
    if index.empty or index.tz is None:
        raise ValueError("Timezone-aware five-minute observation clock required")
    if not index.equals(pd.date_range(index[0], index[-1], freq="5min")) or any(
        t.minute % 5 or t.second or t.microsecond for t in index
    ):
        raise ValueError("Complete increasing five-minute clock required")
    if not pd.DatetimeIndex(observations.available_at).equals(index + pd.Timedelta(minutes=5)):
        raise ValueError("Minute observation must be available at its five-minute boundary")
    volume = observations[["base_volume", "quote_volume"]].to_numpy(dtype=float)
    price = observations.vwap.to_numpy(dtype=float)
    if not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError("Finite nonnegative actual minute volumes required")
    zero = volume[:, 0] == 0
    if (volume[zero, 1] != 0).any() or not np.array_equal(np.isnan(price), zero):
        raise ValueError("Zero-volume observations are UNKNOWN, never filled prices")
    if not np.isfinite(price[~zero]).all() or (price[~zero] <= 0).any():
        raise ValueError("Finite positive observed VWAP required")
    if not np.allclose(price[~zero], volume[~zero, 1] / volume[~zero, 0], rtol=0, atol=0):
        raise ValueError("VWAP must equal observed quote volume divided by base volume")
    returns = pd.Series(np.log(price), index=index).diff()
    squared = returns**2
    rv = squared.resample("D").sum(min_count=288)
    up = squared.where(returns > 0, 0).where(returns.notna()).resample("D").sum(min_count=288)
    down = squared.where(returns < 0, 0).where(returns.notna()).resample("D").sum(min_count=288)
    result = pd.DataFrame({"rv": rv, "up": up, "down": down})
    result["sj"] = up - down
    denominator = rv.where(rv > 0)
    result["rsj"] = result.sj / denominator
    result["rsk"] = np.sqrt(288) * (returns**3).resample("D").sum(min_count=288) / denominator**1.5
    result["rkt"] = 288 * (returns**4).resample("D").sum(min_count=288) / denominator**2
    result["day_return"] = returns.resample("D").sum(min_count=288)
    result["observed_returns"] = returns.notna().resample("D").sum()
    if np.isinf(result.to_numpy()).any():
        raise ValueError("Undefined realized variation must remain UNKNOWN")
    return result
