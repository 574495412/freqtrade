"""Local past-window PCA/OU diagnostic, not an upstream trading strategy.

Implements standard correlation PCA, ridge residuals and discrete AR(1) math.
No copied external account engine, gap filling, wallet, orders or allocation.
The reference kernel's residual demeaning is retained: the terminal cumulative
residual is therefore near zero, rather than a new observed price divergence.
"""

import numpy as np
import pandas as pd


def _prices(frame):
    if not isinstance(frame, pd.DataFrame) or frame.shape[1] < 3 or len(frame) < 240:
        raise ValueError("At least three fixed, named price series required")
    if not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC":
        raise ValueError("UTC hourly native price index required")
    if not frame.columns.is_unique or not frame.index.is_unique:
        raise ValueError("Unique asset/time keys required")
    if not frame.index.as_unit("ns").equals(
        pd.date_range(frame.index[0], frame.index[-1], freq="h").as_unit("ns")
    ):
        raise ValueError("Full observed hourly price calendar required")
    if any(
        pd.api.types.is_bool_dtype(d) or not pd.api.types.is_numeric_dtype(d) for d in frame.dtypes
    ):
        raise ValueError("Finite numeric, non-boolean observed prices required")
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("All raw prices must be finite and positive; no filling")
    return values


def pca_ou_window(window):
    """One fixed 240-price/239-return observation window, already completed."""
    prices = np.asarray(window, dtype=float)
    if prices.ndim != 2 or prices.shape[0] != 240 or prices.shape[1] < 3:
        raise ValueError("Exactly240completed prices and at least3assets required")
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("No missing/nonpositive price interpolation allowed")
    returns = np.diff(np.log(prices), axis=0)
    scale = returns.std(axis=0, ddof=1)
    if (scale <= 0).any():
        raise ValueError("Fixed universe contains constant return series")
    standardized = (returns - returns.mean(axis=0)) / scale
    correlation = standardized.T @ standardized / (len(returns) - 1)
    values, vectors = np.linalg.eigh(correlation)
    order = np.argsort(values)[::-1][:2]
    portfolios = vectors[:, order] / scale[:, None]
    portfolios /= np.abs(portfolios).sum(axis=0)
    design = np.column_stack((np.ones(len(returns)), returns @ portfolios))
    betas = np.linalg.solve(design.T @ design + 1e-5 * np.eye(3), design.T @ returns)
    residuals = returns - design @ betas
    residuals -= residuals.mean(axis=0)
    process = residuals.cumsum(axis=0)
    previous, following = process[:-1], process[1:]
    centered = previous - previous.mean(axis=0)
    denominator = (centered * centered).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        b = (centered * (following - following.mean(axis=0))).sum(axis=0) / denominator
        a = following.mean(axis=0) - b * previous.mean(axis=0)
        innovation = following - a - b * previous
        variance = innovation.var(axis=0, ddof=1)
        equilibrium_mean = a / (1 - b)
        equilibrium_sd = np.sqrt(variance / (1 - b * b))
        score = -(process[-1] - equilibrium_mean) / equilibrium_sd
    admissible = (b > 0) & (b < 1) & (equilibrium_sd > 0) & np.isfinite(score)
    score = np.where(admissible, score, np.nan)
    return {
        "pca_ou_reversal": score,
        "ou_b": b,
        "ou_a": a,
        "ou_mean": equilibrium_mean,
        "ou_equilibrium_sd": equilibrium_sd,
        "terminal_cumulative_residual": process[-1],
        "reversal1": -returns[-1],
        "reversal239": -returns.sum(axis=0),
        "lowvol239": -scale,
        "pca_eigenvalues": values[order],
    }


def pca_ou_diagnostics(close, decisions):
    """At D use only bars D-240h through D-1h; never the current D close."""
    prices = _prices(close)
    decisions = pd.DatetimeIndex(decisions)
    if (
        str(decisions.tz) != "UTC"
        or not decisions.is_unique
        or not decisions.is_monotonic_increasing
    ):
        raise ValueError("Unique increasing UTC decision clocks required")
    columns = [
        "pca_ou_reversal",
        "ou_b",
        "ou_a",
        "ou_mean",
        "ou_equilibrium_sd",
        "terminal_cumulative_residual",
        "reversal1",
        "reversal239",
        "lowvol239",
    ]
    result = {
        key: pd.DataFrame(index=decisions, columns=close.columns, dtype=float) for key in columns
    }
    for decision in decisions:
        end = close.index.searchsorted(decision, side="left")
        if end < 240 or close.index[end - 1] != decision - pd.Timedelta(hours=1):
            raise ValueError("Missing complete240hour source window")
        row = pca_ou_window(prices[end - 240 : end])
        for key in columns:
            result[key].loc[decision] = row[key]
    return result
