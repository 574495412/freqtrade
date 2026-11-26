"""Local native HAR-RV forecast interface; no account simulator.

Source math/config: waelsabri11/intraday-volatility-research 80059675020e.
Native integration separates known feature eligibility from future-target scoring.
"""

import numpy as np
import pandas as pd

HAR = ["lrv", "lrv_w", "lrv_m"]
BASE = [
    "lrv",
    "lbv",
    "ljump_share",
    "lrs_neg",
    "rs_neg_share",
    "log_qv",
    "log_trades",
    "imb",
    "abs_imb",
    "cs",
    "roll",
    "log_amihud",
    "rv_last4h_share",
    "ret",
]
GBM = BASE + ["lrv_w", "lrv_m", "imb_w", "dow_next"]


def known_feature_mask(daily):
    """Source common past-feature mask, without its future-target evaluation mask."""
    cols = sorted(set(HAR + BASE + GBM))
    current = daily[cols].notna().all(axis=1)
    base_valid = daily[BASE].notna().all(axis=1)
    sequence = base_valid.rolling(30, min_periods=30).sum().eq(30)
    selected = current & sequence
    if not np.isfinite(daily.loc[selected, cols].to_numpy()).all():
        raise ValueError("Infinite known features cannot enter native forecast")
    return selected.to_numpy()


def har_forecasts(daily, mode="HAR"):
    """Source730/30expanding forecasts indexed by completed UTC origin day.

    Fit at origin s uses rows <s: target of row s-1 is observed at end of s.
    Forecasting origin s never tests availability of its not-yet-known target s+1.
    """
    if mode not in ["HAR", "Naive"]:
        raise ValueError("Unregistered source model")
    ix = daily.index
    if (
        not isinstance(ix, pd.DatetimeIndex)
        or ix.tz is not None
        or not ix.as_unit("ns").equals(pd.date_range(ix[0], ix[-1], freq="D").as_unit("ns"))
    ):
        raise ValueError("Contiguous naive UTC daily source calendar required")
    X = daily[HAR].to_numpy()
    y = daily.target_lrv.to_numpy()
    known = known_feature_mask(daily)
    train_valid = known & np.isfinite(y)
    out = np.full(len(daily), np.nan)
    fits = []
    for origin in range(730, len(daily), 30):
        train = np.flatnonzero(train_valid[:origin])
        test = np.arange(origin, min(origin + 30, len(daily)))
        test = test[known[test]]
        if len(train) < 100 or len(test) == 0:
            continue
        if train[-1] >= origin or not np.allclose(
            y[train], daily.lrv.to_numpy()[train + 1], rtol=1e-12, atol=1e-12
        ):
            raise ValueError("Unrealised or incorrectly aligned training labels")
        design = np.c_[np.ones(len(train)), X[train]]
        beta, *_ = np.linalg.lstsq(design, y[train], rcond=None)
        smearing = float(np.exp(y[train] - design @ beta).mean())
        out[test] = (
            np.exp(np.c_[np.ones(len(test)), X[test]] @ beta) * smearing
            if mode == "HAR"
            else np.exp(X[test, 0])
        )
        fits.append(
            dict(
                origin=str(ix[origin]),
                train_rows=len(train),
                last_train_feature_day=str(ix[train[-1]]),
                latest_realised_target_day=str(ix[train[-1] + 1]),
                first_forecast_day=str(ix[test[0]]),
                last_forecast_day=str(ix[test[-1]]),
                beta=beta.tolist(),
                smearing=smearing,
            )
        )
    selected = out[np.isfinite(out)]
    if len(selected) and not (selected > 0).all():
        raise ValueError("Nonpositive variance forecast")
    return pd.Series(out, index=ix, name="forecast_rv"), fits


def source_half_weights(daily, mode="HAR"):
    """Published40%/1x/no-band per-asset rule, allocated half of common wallet."""
    forecasts, fits = har_forecasts(daily, mode)
    weight = 0.5 * np.clip(0.4 / np.sqrt(forecasts * 365), 0, 1)
    weight.index = (weight.index + pd.Timedelta(days=1)).tz_localize("UTC")
    return weight, fits, forecasts
