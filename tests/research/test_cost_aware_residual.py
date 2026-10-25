"""Forecast label cash boundaries, fixed purging and past-only predictor inputs."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from research.crypto.cost_aware_residual import (
    FEATURES,
    feature_rows,
    horizon_labels,
    maturity_mask,
    opportunities,
)


def test_unit_labels_charge_both_fees_and_only_entry_inclusive_exit_exclusive_funding():
    clock = pd.date_range("2024-01-01", periods=20, freq="5min", tz="UTC")
    price = pd.DataFrame({"open": [100.0] * 14 + [110.0] * 6}, index=clock)
    rows = pd.DataFrame({"feature_open": [clock[0], clock[0]], "opportunity_side": [1, -1]})
    events = clock[[0, 2, 6, 14, 19]].as_unit("ms").asi8
    labels = horizon_labels(price, rows, events, np.array([0.0, 0.2, 0.1, 100.0, 0.0]), 12, 0.0008)
    fees = Decimal("0.0008") * (1 + Decimal(110) / 100)
    for index, sign in enumerate([1, -1]):
        expected = sign * (Decimal(110) / 100 - 1) - fees - sign * Decimal("0.3") / 100
        assert labels.unit_net_horizon_label.iloc[index] == pytest.approx(
            float(expected), abs=1e-14
        )
    assert labels.label_known_at_ms.eq(clock[14].value // 1_000_000).all()


def test_fixed_embargo_does_not_admit_late_rows_because_their_outcome_finished_early():
    dates = pd.to_datetime(["2024-06-29 23:50Z", "2024-06-29 23:55Z"])
    rows = pd.DataFrame({"date": dates, "finite_features": True, "label_known_at_ms": 0})
    assert maturity_mask(
        rows, pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-07-01", tz="UTC")
    ).tolist() == [True, False]


def panel(clock):
    return pd.DataFrame(
        {
            "date": clock + pd.Timedelta(minutes=5),
            "feature_open": clock,
            "small_long": [True, False],
            "small_short": [False, True],
            "core_long": [True, False],
            "core_short": [False, True],
            "regime_long": [True, True],
            "regime_short": [False, False],
            "residual_z": [-2.1, 2.7],
            "residual_sigma": 0.01,
            "ar1_phi": 0.99,
            "feature_atr": 1.0,
            "beta": 1.2,
        }
    )


def test_core_momentum_priority_and_failed_core_trend_leave_the_small_opportunity():
    clock = pd.date_range("2024-01-01", periods=2, freq="5min", tz="UTC")
    result = opportunities(panel(clock))
    assert result.opportunity_side.tolist() == [1, 1]
    assert result.opportunity_sleeve.tolist() == ["small", "core"]


def test_future_prices_and_volumes_do_not_change_earlier_predictor_features():
    clock = pd.date_range("2024-01-01", periods=50, freq="5min", tz="UTC")
    price = pd.DataFrame({"close": np.linspace(100, 120, 50), "volume": 1.0}, index=clock)
    btc = pd.DataFrame({"close": np.linspace(1000, 1030, 50)}, index=clock)
    source = panel(clock[[30, 32]])
    before = feature_rows(source, price, btc)[FEATURES]
    changed = price.copy()
    changed.loc[clock[33] :, ["close", "volume"]] *= 100
    pd.testing.assert_frame_equal(
        before, feature_rows(source, changed, btc)[FEATURES], check_exact=True
    )
