"""Future-volume, exit-persistence and dust constraints for composed accounts."""

from copy import deepcopy

import pandas as pd

from research.crypto.factor_portfolio import replay


def data():
    clocks = pd.date_range("2025-01-01", "2025-01-02", freq="5min", inclusive="left", tz="UTC")
    bars = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "quote_volume": 100000.0},
        index=clocks,
    )
    scores = pd.DataFrame(
        {"BTCUSDT": 0.1, "vol_BTCUSDT": 0.01},
        index=pd.date_range("2025-01-01", "2025-01-02", freq="h", inclusive="left", tz="UTC"),
    )
    funding = pd.DataFrame(
        {"funding_rate": [0.001], "mark": [100.0]}, index=pd.DatetimeIndex(["2025-01-01T08:00Z"])
    )
    config = {
        "capital": 10000,
        "fee_bps": 5,
        "slippage_bps": 3,
        "participation": 0.01,
        "drawdown_halt": 0.15,
        "rebalance_hours": 1,
        "gross": 0.8,
        "threshold": 0.0016,
        "max_positions": 1,
        "stop_loss": 0.08,
    }
    return {"BTCUSDT": bars}, {"BTCUSDT": funding}, scores, config


def test_future_volume_changes_fills_but_not_submitted_decision():
    b, f, s, c = data()
    low = deepcopy(b)
    low["BTCUSDT"].loc["2025-01-01T01:00Z", "quote_volume"] = 1
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T02:00")
    other = replay(low, f, s, c, "2025-01-01T01:00", "2025-01-01T02:00")
    assert a["requests"] == other["requests"]
    assert a["events"][0]["quantity"] > other["events"][0]["quantity"]


def test_zero_capacity_stop_cannot_be_cleared_by_a_rebalance():
    b, f, s, c = data()
    b["BTCUSDT"].loc["2025-01-01T07:55Z", "low"] = 80
    b["BTCUSDT"].loc["2025-01-01T08:00Z":"2025-01-01T08:15Z", "quote_volume"] = 0
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T08:15")
    assert a["pending_exits"] and a["positions"]
    assert any(e["type"] == "funding" and e["cash_delta"] < 0 for e in a["events"])
    extended = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T09:00")
    assert extended["capacity"]["max_exit_delay_minutes"] >= 20
    assert any(e.get("reason") == "stop" for e in extended["events"])


def test_cash_control_has_no_fills_fees_or_invented_closed_trades():
    b, f, s, c = data()
    c["control"] = "cash"
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T02:00")
    assert not a["events"] and not a["trades"]
    assert a["curve"].equity.eq(10000).all()
