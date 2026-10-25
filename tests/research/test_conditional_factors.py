"""Canonical clocks, delayed inputs, conservative labels and purged learning."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.data import read
from research.crypto.conditional_factors import barrier_labels, features
from research.crypto.conditional_learning import fit_model, mature_rows, predict
from research.crypto.prepare_conditional_metrics import FIELDS, parse_metrics
from research.crypto.strategies.funding_rotation import load_factor


def zipped_metrics(times):
    frame = pd.DataFrame({"create_time": times, "symbol": "BTCUSDT"})
    for field in FIELDS:
        frame[field] = "100"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("metrics.csv", frame.to_csv(index=False))
    return buffer.getvalue()


def test_source_order_does_not_change_canonical_metrics_values():
    times = ["2024-01-01 00:00:00", "2024-01-01 00:05:00", "2024-01-01 00:10:00"]
    ordered = parse_metrics(zipped_metrics(times), "BTCUSDT", "2024-01-01")
    shuffled = parse_metrics(
        zipped_metrics([times[2], times[0], times[1]]), "BTCUSDT", "2024-01-01"
    )
    pd.testing.assert_frame_equal(ordered, shuffled)


def test_duplicate_and_off_grid_source_clocks_are_not_silently_coalesced():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_metrics(zipped_metrics(["2024-01-01 00:00:00"] * 2), "BTCUSDT", "2024-01-01")
    with pytest.raises(ValueError, match="five-minute"):
        parse_metrics(zipped_metrics(["2024-01-01 00:01:00"]), "BTCUSDT", "2024-01-01")


def inputs():
    rng = np.random.default_rng(151)
    dates = pd.date_range("2024-01-01", periods=900, freq="5min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(dates))))
    quote = rng.uniform(1e6, 2e6, len(dates))
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.9998,
            "high": close * 1.003,
            "low": close * 0.997,
            "close": close,
            "volume": quote / close,
            "quote_volume": quote,
            "taker_buy_quote": quote * rng.uniform(0.2, 0.8, len(dates)),
        }
    )
    metrics = pd.DataFrame(
        {
            "raw_time": dates,
            "sum_open_interest": 100.0,
            "sum_open_interest_value_status": "KNOWN_RAW_VALUE",
        }
    )
    events = [
        {"symbol": "BTCUSDT", "time_ms": int(t.timestamp() * 1000), "rate": 0.0001}
        for t in pd.date_range("2023-12-29", "2024-01-05", freq="8h", inclusive="left", tz="UTC")
    ]
    source = read(Path("research/crypto/funding_rotation_plan.json"))["factor_source"]
    factor = load_factor(Path(source["path"]), source["sha256"])
    return frame, metrics, events, factor.calculate


def calculate(frame, metrics, events, funding):
    btc = pd.Series(frame.close.to_numpy(), index=pd.DatetimeIndex(frame.date))
    return features(
        frame,
        metrics,
        events,
        btc,
        funding,
        int(pd.Timestamp("2023-12-29T00:00Z").timestamp() * 1000),
        int(pd.Timestamp("2024-01-05T00:00Z").timestamp() * 1000) - 1,
    )


def test_future_prices_flow_funding_and_oi_cannot_change_past_features():
    frame, metrics, events, funding = inputs()
    full = calculate(frame, metrics, events, funding)
    changed = frame.copy()
    changed.loc[650:, ["close", "high", "low", "open"]] *= 10
    changed.loc[650:, "taker_buy_quote"] = 0
    altered_metrics = metrics.copy()
    altered_metrics.loc[650:, "sum_open_interest"] = 1
    altered_events = [
        dict(e, rate=-0.1) if e["time_ms"] >= int(frame.date.iloc[650].timestamp() * 1000) else e
        for e in events
    ]
    comparison = calculate(changed, altered_metrics, altered_events, funding)
    pd.testing.assert_frame_equal(full.iloc[:650], comparison.iloc[:650])


def test_raw_oi_only_becomes_available_after_registered_ten_minute_delay():
    frame, metrics, events, funding = inputs()
    metrics.loc[400, "sum_open_interest"] = 50
    result = calculate(frame, metrics, events, funding)
    assert result.oi_contraction60.iloc[400] == 0
    assert result.oi_contraction60.iloc[401] == pytest.approx(np.log(2))
    metrics.loc[400, "sum_open_interest"] = 0
    assert np.isnan(calculate(frame, metrics, events, funding).oi_contraction60.iloc[401])


def candles():
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=10, freq="5min", tz="UTC"),
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
        }
    )


def test_ambiguous_bar_uses_loss_and_waits_for_completed_outcome_bar():
    frame = candles()
    frame.loc[2, ["high", "low"]] = [102, 98]
    labels = barrier_labels(frame, 1, 0.01, 0.01, 2, 0.0008, [], [])
    assert labels.net_return_label.iloc[0] == pytest.approx(-0.011592)
    assert labels.label_known_at_ms.iloc[0] == int(frame.date.iloc[3].timestamp() * 1000)


@pytest.mark.parametrize("sign,price,expected", [(1, 97, -0.031576), (-1, 103, -0.031624)])
def test_gap_loss_is_not_filled_at_the_better_stop_price(sign, price, expected):
    frame = candles()
    frame.loc[3, ["open", "high", "low"]] = [price, price + 0.1, price - 0.1]
    labels = barrier_labels(frame, sign, 0.01, 0.01, 2, 0.0008, [], [])
    assert labels.net_return_label.iloc[0] == pytest.approx(expected)


@pytest.mark.parametrize("sign", [1, -1])
def test_label_funding_is_only_charged_during_its_exposure(sign):
    frame = candles()
    timestamps = [
        frame.date.iloc[1].value // 1_000_000,
        frame.date.iloc[2].value // 1_000_000 + 1000,
        frame.date.iloc[4].value // 1_000_000,
    ]
    result = barrier_labels(frame, sign, 0.01, 0.01, 2, 0, timestamps, [0.1, 0.1, 0.1])
    assert result.net_return_label.iloc[0] == pytest.approx(-sign * 0.001)


def test_training_boundary_purges_whole_horizon_without_using_early_exit():
    dates = pd.date_range("2024-06-29", periods=72, freq="h", tz="UTC")
    early = dates.as_unit("ms").asi8 + 60_000
    mask = mature_rows(dates, early, "2024-01-01", "2024-07-01", 288)
    assert mask[:23].all()
    assert not mask[24:].any()


def test_prediction_cannot_refit_scaling_from_future_observations():
    rng = np.random.default_rng(302)
    x = rng.normal(size=(1000, 3))
    win = (x[:, 0] + rng.normal(size=1000) > 0).astype(float)
    model = fit_model(x, 0.01 * x[:, 0], win)
    frozen = json.dumps(model, sort_keys=True)
    probabilities, returns = predict(model, np.array([[-1, 0, 0], [1, 0, 0], [1e9, 0, 0]]))
    assert probabilities[0] < probabilities[1] < probabilities[2]
    assert returns[0] < returns[1]
    assert json.dumps(model, sort_keys=True) == frozen
