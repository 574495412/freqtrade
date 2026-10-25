"""Independent rolling-regression and AR1 checks, including self-regression degeneracy."""

import numpy as np
import pandas as pd
import pytest

from research.crypto.btc_residual import events, features


def prices():
    rng = np.random.default_rng(19)
    x = 8 + np.cumsum(rng.normal(0, 0.004, 500))
    e = np.zeros(500)
    for i in range(1, 500):
        e[i] = 0.9 * e[i - 1] + rng.normal(0, 0.008)
    y = 1 + 1.3 * x + e
    clock = pd.date_range("2024-01-01", periods=500, freq="5min", tz="UTC")

    def frame(v):
        c = np.exp(v)
        return pd.DataFrame(
            {"date": clock, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}
        )

    return frame(y), frame(x)


def test_rolling_parameters_match_scalar_fixed_beta_regression_and_ar1():
    price, btc = prices()
    computed = features(price, btc, window=96)
    for t in [100, 250, 499]:
        y, x = np.log(price.close.to_numpy()), np.log(btc.close.to_numpy())
        a, b = y[t - 96 : t], x[t - 96 : t]
        coefficient = np.linalg.lstsq(np.column_stack([np.ones(96), b]), a, rcond=None)[0]
        residuals = a - coefficient[1] * b
        lagged = y[t - 97 : t - 1] - coefficient[1] * x[t - 97 : t - 1]
        phi = np.linalg.lstsq(np.column_stack([np.ones(96), lagged]), residuals, rcond=None)[0][1]
        residual = y[t] - coefficient[0] - coefficient[1] * x[t]
        assert computed.beta.iloc[t] == pytest.approx(coefficient[1], abs=1e-8)
        assert computed.residual.iloc[t] == pytest.approx(residual, abs=1e-8)
        assert computed.ar1_phi.iloc[t] == pytest.approx(phi, abs=1e-7)


def test_future_bars_do_not_change_past_residual_signals():
    price, btc = prices()
    full = events(features(price, btc, window=96))
    cut = events(features(price.iloc[:300], btc.iloc[:300], window=96))
    pd.testing.assert_frame_equal(full.iloc[:300], cut, check_exact=True)


def test_self_regression_and_flat_reference_cannot_generate_alpha():
    price, btc = prices()
    same = events(features(btc, btc, window=96))
    flat = btc.copy()
    flat["close"] = 100.0
    unknown = events(features(price, flat, window=96))
    for frame in [same, unknown]:
        assert frame.residual_z.isna().all()
        assert not frame[["small_long", "small_short", "core_long", "core_short"]].any().any()
