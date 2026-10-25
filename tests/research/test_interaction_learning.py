"""Causal and statistical failure cases for the interaction research pipeline."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.data import sha
from research.crypto.interaction_features import funding_features, hourly_features
from research.crypto.interaction_models import (
    calibrate,
    enable_runtime,
    fit_bundle,
    fit_calibration,
    predict_bundle,
    scalar_predict,
)
from research.crypto.run_interaction_learning import select_predictions
from research.crypto.screen_interaction_learning import bh_adjust, block_statistics


def test_appending_future_candles_cannot_change_past_features():
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 1800)))
    frame = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": rng.uniform(1, 100, 1800),
        },
        index=pd.date_range("2024-01-01", periods=1800, freq="5min", tz="UTC"),
    )
    prefix = hourly_features(frame.iloc[:1200])
    full = hourly_features(frame)
    pd.testing.assert_frame_equal(full.loc[prefix.index], prefix, check_exact=True)


def test_funding_event_is_delayed_and_only_past_interval_sets_normalization():
    dates = pd.date_range("2024-01-01", periods=4, freq="8h", tz="UTC")
    frame = pd.DataFrame({"date": dates, "funding_rate": [0.001, 0.002, 0.003, 0.004]})
    result = funding_features(frame)
    assert list(result.available_at) == list(dates + pd.Timedelta(hours=1))
    assert result.rate8.iloc[2] == pytest.approx(0.003)
    assert result.change8.iloc[2] == pytest.approx(0.001)
    pd.testing.assert_frame_equal(
        result.iloc[:3], funding_features(frame.iloc[:3]), check_exact=True
    )


def test_probability_calibration_cannot_invert_a_failed_classifier():
    p = np.linspace(0.05, 0.95, 1000)
    y = (p < 0.5).astype(int)
    model = fit_calibration(p, y, 0.001)
    assert model["slope"] == pytest.approx(0, abs=1e-7)
    assert np.allclose(calibrate(model, p), 0.5, atol=1e-6)


def test_selection_uses_forecasts_and_core_preempts_same_pair_small():
    rows = pd.DataFrame(
        {
            "model": ["A"] * 4,
            "symbol": ["ETHUSDT"] * 4,
            "date": [pd.Timestamp("2024-07-01", tz="UTC")] * 4,
            "side": [1, -1, 1, -1],
            "sleeve": ["small", "small", "core", "core"],
            "probability": [0.9, 0.9, 0.7, 0.7],
            "predicted_net": [0.003, 0.004, 0.002, 0.003],
            "future_label": [100, -100, 100, -100],
        }
    )
    config = {
        "minimum_probability": {"small": 0.8, "core": 0.55},
        "minimum_predicted_net": {"small": 0.0008, "core": 0.0016},
    }
    result = select_predictions(rows, config)
    assert len(result) == 1
    assert result.iloc[0].sleeve == "core" and result.iloc[0].side == -1


def test_no_units_are_not_a_profitable_cash_strategy_and_zero_labels_fail():
    dates = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    config = {
        "bootstrap_replicates": 100,
        "block_days": 7,
        "minimum_evaluation_units": 30,
        "minimum_distinct_days": 30,
    }
    empty = block_statistics(pd.DataFrame(), np.array([]), dates, config, 42)
    assert empty["mean_net_unit_label"] is None and empty["one_sided_block_pvalue"] == 1
    result = block_statistics(pd.DataFrame({"date": dates}), np.zeros(60), dates, config, 42)
    assert result["one_sided_block_pvalue"] == 1
    positive = block_statistics(
        pd.DataFrame({"date": dates}), np.full(60, 0.002), dates, config, 42
    )
    assert positive["block_ci_95"][0] > 0 and positive["one_sided_block_pvalue"] < 0.05


def test_bh_adjustment_uses_the_whole_comparison_family():
    actual = bh_adjust([0.01, 0.04, 0.03, 1.0])
    np.testing.assert_allclose(actual, [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 1.0])


def test_histogram_predictions_match_scalar_tree_traversal():
    manifest = Path("user_data/crypto_quant/dependencies/sklearn_1_9_1_manifest.json")
    enable_runtime({"runtime_manifest": {"path": str(manifest), "sha256": sha(manifest)}})
    from threadpoolctl import threadpool_limits

    rng = np.random.default_rng(7)
    x = rng.normal(size=(1800, 3))
    y = 0.01 * np.sin(x[:, 0]) + 0.001 * x[:, 1]
    config = {
        "boosting_parameters": {
            "max_iter": 4,
            "max_leaf_nodes": 7,
            "max_depth": 3,
            "min_samples_leaf": 20,
            "early_stopping": False,
            "random_state": 7,
        },
        "calibration_penalty": 0.001,
    }
    with threadpool_limits(limits=2):
        model = fit_bundle("boosted", x[:1200], y[:1200], x[1200:1500], y[1200:1500], config)
        raw, probability, net = predict_bundle(model, x[1500:1505])
        for i, row in enumerate(x[1500:1505]):
            np.testing.assert_allclose(
                scalar_predict(model, row), [raw[i], probability[i], net[i]], rtol=1e-10, atol=1e-12
            )
