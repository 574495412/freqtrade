"""Source-default buffering uses actual positions; hard risk still takes priority."""

import pandas as pd

from research.crypto.regime_portfolio import replay as original
from research.crypto.sizing_portfolio import apply_forecast_buffer, replay
from tests.research.test_regime_portfolio import fixture


def test_nearest_band_edge_and_no_trade_zone_use_actual_marked_inventory():
    row = {"buffer_A": 0.025}
    marks = {"A": 100.0}
    target = {"A": 0.4}
    inside = {"A": {"side": 1, "q": 41.0}}
    assert abs(apply_forecast_buffer(target, row, inside, marks, 10000, 0.8)["A"] - 0.41) < 1e-12
    outside = {"A": {"side": 1, "q": 45.0}}
    assert abs(apply_forecast_buffer(target, row, outside, marks, 10000, 0.8)["A"] - 0.42) < 1e-12
    assert apply_forecast_buffer({"A": 0.01}, row, {}, marks, 10000, 0.8) == {}


def test_unbuffered_controls_reproduce_the_frozen_engine_exactly():
    b, f, s, c = fixture()
    a = original(b, f, s, c, "2025-01-01T01:00", "2025-01-01T12:00")
    copied = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T12:00")
    pd.testing.assert_frame_equal(a["curve"], copied["curve"])
    assert a["events"] == copied["events"] and a["resume_state"] == copied["resume_state"]


def test_event_risk_change_bypasses_buffer_and_does_not_keep_excess_size():
    b, f, s, c = fixture()
    c["position_buffer"] = True
    s["buffer_BTCUSDT"] = 0.5
    s.loc["2025-01-01T03:00Z":"2025-01-01T04:00Z", "risk_scale"] = 0.5
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T06:00")
    curve = a["curve"].set_index("date")
    assert curve.loc["2025-01-01T03:05Z", "gross_exposure"] < 4100
    assert curve.loc["2025-01-01T05:05Z", "gross_exposure"] > 7900


def test_risk_budget_overrides_a_band_that_would_retain_too_much_gross():
    held = {"A": {"side": 1, "q": 60.0}, "B": {"side": -1, "q": 50.0}}
    result = apply_forecast_buffer(
        {"A": 0.4, "B": -0.4},
        {"buffer_A": 0.5, "buffer_B": 0.5},
        held,
        {"A": 100.0, "B": 100.0},
        10000,
        0.8,
    )
    assert abs(sum(abs(v) for v in result.values()) - 0.8) < 1e-12
