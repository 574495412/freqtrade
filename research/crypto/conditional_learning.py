"""Fixed-penalty probability and net-label regression, with chronological purging."""

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit


def mature_rows(dates, known_ms, start, end, maximum_bars):
    dates = pd.DatetimeIndex(dates)
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    # A fixed worst-case horizon avoids selecting training rows by future early exits.
    horizon = pd.Timedelta(minutes=10 + 5 * maximum_bars)
    return (
        (dates >= lower)
        & (dates + horizon < upper)
        & np.isfinite(known_ms)
        & (known_ms < upper.value // 1_000_000)
    )


def fit_model(x, net_return, net_win, penalty=0.01, maximum_iterations=200):
    x = np.asarray(x, dtype=float)
    net_return, net_win = np.asarray(net_return), np.asarray(net_win)
    if len(x) < 30 or not np.isfinite(x).all() or not np.isfinite(net_return).all():
        raise ValueError("Insufficient or nonfinite fixed training population")
    if not np.isin(net_win, [0, 1]).all() or len(np.unique(net_win)) != 2:
        raise ValueError("Both net outcome classes required")
    mean, deviation = x.mean(axis=0), x.std(axis=0)
    deviation = np.where(deviation > 0, deviation, 1.0)
    design = np.column_stack([np.ones(len(x)), (x - mean) / deviation])
    ridge_penalty = np.eye(design.shape[1]) * penalty
    ridge_penalty[0, 0] = 0
    linear = np.linalg.solve(
        design.T @ design / len(x) + ridge_penalty,
        design.T @ net_return / len(x),
    )

    def loss(weights):
        scores = design @ weights
        objective = np.mean(np.logaddexp(0, scores) - net_win * scores)
        objective += 0.5 * penalty * (weights[1:] @ weights[1:])
        gradient = design.T @ (expit(scores) - net_win) / len(x)
        gradient[1:] += penalty * weights[1:]
        return objective, gradient

    initial = np.zeros(design.shape[1])
    rate = net_win.mean()
    initial[0] = np.log(rate / (1 - rate))
    optimized = minimize(
        loss,
        initial,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": maximum_iterations, "gtol": 1e-7},
    )
    if not optimized.success or not np.isfinite(optimized.x).all():
        raise ValueError(f"Unconverged logistic fit: {optimized.message}")
    return {
        "mean": mean.tolist(),
        "deviation": deviation.tolist(),
        "linear": linear.tolist(),
        "logistic": optimized.x.tolist(),
        "fit_rows": len(x),
        "training_win_rate": float(rate),
        "iterations": int(optimized.nit),
        "converged": True,
    }


def predict(model, x):
    x = np.asarray(x, dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Prediction population must be explicitly complete")
    design = np.column_stack(
        [np.ones(len(x)), (x - np.asarray(model["mean"])) / np.asarray(model["deviation"])]
    )
    return expit(design @ model["logistic"]), design @ model["linear"]
