"""Past-only rolling log-price residuals and AR(1) recovery-speed diagnostics."""

import numpy as np
import pandas as pd


def features(price, reference, window=4032):
    if not price.date.equals(reference.date):
        raise ValueError("Exact BTC and asset bar clocks required")
    frame = price.copy()
    y, x = np.log(price.close), np.log(reference.close)
    a, b, old_a, old_b = y.shift(1), x.shift(1), y.shift(2), x.shift(2)

    def mean(v):
        return v.rolling(window, min_periods=window).mean()

    def covariance(v, w):
        return v.rolling(window, min_periods=window).cov(w, ddof=0)

    vx, vy, xy = covariance(b, b), covariance(a, a), covariance(a, b)
    beta = xy / vx.where(vx > 1e-10)
    center = mean(a) - beta * mean(b)
    residual_variance = vy + beta**2 * vx - 2 * beta * xy
    sigma = np.sqrt(residual_variance.where(residual_variance > 1e-10))
    residual = y - beta * x - center
    old_variance = (
        covariance(old_a, old_a)
        + beta**2 * covariance(old_b, old_b)
        - 2 * beta * covariance(old_a, old_b)
    )
    lag_covariance = (
        covariance(a, old_a)
        + beta**2 * covariance(b, old_b)
        - beta * (covariance(a, old_b) + covariance(b, old_a))
    )
    phi = lag_covariance / old_variance.where(old_variance > 1e-10)
    half_life_hours = (np.log(0.5) / np.log(phi.where(phi.between(1e-12, 1 - 1e-12)))) / 12
    previous = frame.close.shift(1)
    atr = (
        pd.concat(
            [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
            axis=1,
        )
        .max(axis=1)
        .rolling(14, min_periods=14)
        .mean()
    )
    frame["beta"], frame["residual"], frame["residual_sigma"] = beta, residual, sigma
    frame["residual_z"], frame["ar1_phi"] = residual / sigma, phi
    frame["half_life_hours"], frame["feature_atr"] = half_life_hours, atr
    return frame.replace([np.inf, -np.inf], np.nan)


def events(frame):
    frame = frame.copy()
    known = (
        np.isfinite(
            frame[["beta", "residual", "residual_sigma", "residual_z", "ar1_phi", "feature_atr"]]
        ).all(axis=1)
        & frame.beta.between(0.2, 3)
        & frame.feature_atr.gt(0)
        & frame.volume.gt(0)
    )
    for sleeve, threshold, minimum_gap in [("small", 2.0, 0.008), ("core", 2.5, 0.02)]:
        long = known & frame.residual_z.le(-threshold) & frame.residual.le(-minimum_gap)
        short = known & frame.residual_z.ge(threshold) & frame.residual.ge(minimum_gap)
        frame[f"{sleeve}_long"] = long & ~long.shift(1, fill_value=False)
        frame[f"{sleeve}_short"] = short & ~short.shift(1, fill_value=False)
    frame["stable_small"] = frame.half_life_hours.between(0.25, 6)
    frame["stable_core"] = frame.half_life_hours.between(0.5, 24)
    return frame
