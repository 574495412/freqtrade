"""Causal clocks and source score parity; no author account simulator called."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.data.trendcore_signals import (
    _reference_risk_returns,
    _trend_scores,
    trendcore_weights,
)

HERE = Path(__file__).absolute().parent


def prices():
    rng = np.random.default_rng(927)
    ix = pd.date_range("2022-10-01", periods=360 * 24, freq="h", tz="UTC")
    return pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0.00015, 0.006, (len(ix), 2)), axis=0)),
        index=ix,
        columns=["BTC/USDT", "ETH/USDT"],
    )


def test_scores_match_pinned_source():
    path = HERE / "sources/selected/trendcore_spot.py"
    spec = importlib.util.spec_from_file_location("pinned_source_formula", path)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)

    def forbidden(*args, **kw):
        raise AssertionError("Source account simulator forbidden")

    source._backtest = source.backtest = source.stats = forbidden
    p = prices().resample("6h").last()
    pd.testing.assert_frame_equal(_trend_scores(p), source._trend_scores(p), atol=1e-13, rtol=1e-13)


def test_hourly_truncation_and_future_shock():
    p = prices()
    full = trendcore_weights(p)
    for hour in [24 * 300, 24 * 300 + 1, 24 * 300 + 5, 24 * 350 + 4]:
        prefix = p.iloc[: hour + 1]
        w = trendcore_weights(prefix)
        pd.testing.assert_frame_equal(w, full.loc[w.index], atol=1e-13, rtol=1e-13)
        assert w.index.max() <= prefix.index[-1] + pd.Timedelta(hours=1)
        changed = p.copy()
        changed.iloc[hour + 1 :] *= 5
        future = trendcore_weights(changed)
        pd.testing.assert_frame_equal(w, future.loc[w.index], atol=1e-13, rtol=1e-13)


def test_partial_bin_is_not_known_and_full_bin_clock():
    p = prices()
    start = p.index[24 * 300]
    left = trendcore_weights(p.loc[: start + pd.Timedelta(hours=4)])
    right = trendcore_weights(p.loc[: start + pd.Timedelta(hours=5)])
    assert start + pd.Timedelta(hours=6) not in left.index
    assert start + pd.Timedelta(hours=6) in right.index
    assert (right >= 0).all().all() and (right.sum(axis=1) <= 1 + 1e-12).all()
    assert right.iloc[:1023].eq(0).all().all()


def test_invalid_or_missing_hour_is_rejected():
    p = prices()
    with pytest.raises(ValueError, match="contiguous"):
        trendcore_weights(p.drop(p.index[24 * 300]))
    p.iloc[100, 0] = 0
    with pytest.raises(ValueError, match="positive"):
        trendcore_weights(p)


def test_reference_brake_state_has_fixed_source_fee_and_lag():
    ix = pd.date_range("2025-01-01", periods=3, freq="6h", tz="UTC")
    w = pd.DataFrame([[0.5, 0], [0.5, 0], [0, 0]], index=ix)
    r = pd.DataFrame([[0, 0], [0.1, 0], [0, 0]], index=ix)
    out = _reference_risk_returns(w, r)
    assert out.iloc[0] == 0
    assert out.iloc[1] == pytest.approx(0.05 - 0.0005)
    assert out.iloc[2] == 0


def test_timestamp_units_do_not_change_signal():
    p = prices()
    reference = trendcore_weights(p)
    for unit in ["ms", "ns"]:
        q = p.copy()
        q.index = q.index.as_unit(unit)
        w = trendcore_weights(q)
        w.index = w.index.as_unit(reference.index.unit)
        pd.testing.assert_frame_equal(w, reference, check_freq=False)
