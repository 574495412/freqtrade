"""Actual resizing, event restoration, costs, stop priority and restart continuity."""

import json
from copy import deepcopy

import pandas as pd
import pytest

from research.crypto.regime_portfolio import replay, target_weights
from tests.research.test_factor_portfolio import data


def fixture():
    bars, funding, scores, config = data()
    bars["BTCUSDT"]["quote_volume"] = 1e8
    targets = scores.drop(columns=["BTCUSDT", "vol_BTCUSDT"])
    targets["target_BTCUSDT"] = 1.0
    targets["risk_scale"] = 1.0
    config["stop_loss"] = 0.5
    return bars, funding, targets, config


def test_event_risk_reduction_and_restoration_resize_one_complete_campaign():
    b, f, s, c = fixture()
    s.loc["2025-01-01T03:00Z":"2025-01-01T04:00Z", "risk_scale"] = 0.5
    s.loc["2025-01-01T09:00Z":, "target_BTCUSDT"] = 0
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T10:00")
    curve = a["curve"].set_index("date")
    assert curve.loc["2025-01-01T03:05Z", "gross_exposure"] < 4100
    assert curve.loc["2025-01-01T05:05Z", "gross_exposure"] > 7900
    assert any(e.get("addition") for e in a["events"])
    assert len(a["trades"]) == 1 and not a["positions"]
    assert a["trades"][0]["net_pnl"] == pytest.approx(a["cash"] - c["capital"])
    assert sum(e.get("fee", 0) for e in a["events"]) > 10
    assert a["trades"][0]["net_pnl"] < 0


def test_intraday_event_scaling_operates_between_daily_strategy_decisions():
    b, f, s, c = fixture()
    # Use the preceding day for prior-bar history at the daily decision.
    b["BTCUSDT"] = b["BTCUSDT"].shift(freq="-1D").combine_first(b["BTCUSDT"])
    c["rebalance_hours"] = 24
    s.loc["2025-01-01T02:00Z":"2025-01-01T03:00Z", "risk_scale"] = 0.5
    a = replay(b, f, s, c, "2025-01-01T00:00", "2025-01-01T05:00")
    nav = a["curve"].set_index("date")
    assert nav.loc["2025-01-01T02:05Z", "gross_exposure"] < 4100
    assert nav.loc["2025-01-01T04:05Z", "gross_exposure"] > 7900


def test_json_restart_preserves_nav_inventory_funding_high_water_and_counters():
    b, f, s, c = fixture()
    s.loc["2025-01-01T07:00Z":"2025-01-01T09:00Z", "risk_scale"] = 0.5
    whole = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T12:00")
    first = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T08:00")
    state = json.loads(json.dumps(first["resume_state"], allow_nan=False))
    second = replay(b, f, s, c, "2025-01-01T08:00", "2025-01-01T12:00", state)
    combined = pd.concat([first["curve"], second["curve"].iloc[1:]], ignore_index=True)
    pd.testing.assert_frame_equal(whole["curve"], combined)
    assert first["events"] + second["events"] == whole["events"]
    assert first["requests"] + second["requests"] == whole["requests"]
    assert second["resume_state"] == whole["resume_state"]
    wrong = deepcopy(c)
    wrong["slippage_bps"] += 1
    with pytest.raises(ValueError, match="configuration"):
        replay(b, f, s, wrong, "2025-01-01T08:00", "2025-01-01T12:00", state)


def test_stop_during_pending_partial_rebalance_upgrades_to_entire_inventory():
    b, f, s, c = fixture()
    c["stop_loss"] = 0.08
    s.loc["2025-01-01T03:00Z":, "risk_scale"] = 0.5
    b["BTCUSDT"].loc["2025-01-01T03:00Z":"2025-01-01T03:15Z", "quote_volume"] = 0
    b["BTCUSDT"].loc["2025-01-01T03:00Z", "low"] = 80
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T03:15")
    assert a["pending_exits"]["BTCUSDT"]["reason"] == "stop"
    assert a["pending_exits"]["BTCUSDT"]["q"] == a["positions"]["BTCUSDT"]["q"]
    state = json.loads(json.dumps(a["resume_state"]))
    continued = replay(b, f, s, c, "2025-01-01T03:15", "2025-01-01T04:00", state)
    assert not continued["positions"]
    assert continued["trades"][0]["reason"] == "stop"


def test_future_fill_volume_does_not_change_submitted_addition_requests():
    b, f, s, c = fixture()
    s.loc["2025-01-01T03:00Z":"2025-01-01T04:00Z", "risk_scale"] = 0.5
    first = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T05:00")
    state = first["resume_state"]
    low = deepcopy(b)
    low["BTCUSDT"].loc["2025-01-01T05:00Z", "quote_volume"] = 1
    a = replay(b, f, s, c, "2025-01-01T05:00", "2025-01-01T06:00", state)
    other = replay(low, f, s, c, "2025-01-01T05:00", "2025-01-01T06:00", state)
    assert a["requests"] == other["requests"]
    assert a["positions"]["BTCUSDT"]["q"] > other["positions"]["BTCUSDT"]["q"]


def test_target_budget_is_checked_before_any_research_fill():
    with pytest.raises(ValueError, match="target"):
        target_weights({"target_A": 1, "target_B": 1}, ["A", "B"], 0.8, 10)


def test_halted_account_and_pending_exit_cannot_restart_with_a_fresh_high_water():
    b, f, s, c = fixture()
    for col in ("open", "high", "low", "close"):
        b["BTCUSDT"].loc["2025-01-01T07:55Z":, col] = 70
    b["BTCUSDT"].loc["2025-01-01T08:00Z":"2025-01-01T08:15Z", "quote_volume"] = 0
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T08:10")
    assert a["risk_halted"] and a["pending_exits"]
    state = json.loads(json.dumps(a["resume_state"], allow_nan=False))
    continued = replay(b, f, s, c, "2025-01-01T08:10", "2025-01-01T12:00", state)
    assert continued["risk_halted"] and not continued["positions"]
    assert not any(e["type"] == "entry" for e in continued["events"])
    assert continued["resume_state"]["peak"] == state["peak"]


def test_switch_entries_use_only_confirmed_exit_capacity_without_waiting_a_day():
    b, f, s, c = fixture()
    b["ETHUSDT"] = b["BTCUSDT"].copy()
    f["ETHUSDT"] = f["BTCUSDT"].copy()
    s["target_ETHUSDT"] = 0.0
    s.loc["2025-01-01T03:00Z":, "target_BTCUSDT"] = 0.0
    s.loc["2025-01-01T03:00Z":, "target_ETHUSDT"] = 1.0
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T04:00")
    assert "ETHUSDT" in a["positions"] and "BTCUSDT" not in a["positions"]
    blocked = deepcopy(b)
    blocked["BTCUSDT"].loc["2025-01-01T03:00Z":, "quote_volume"] = 0
    other = replay(blocked, f, s, c, "2025-01-01T01:00", "2025-01-01T04:00")
    assert "BTCUSDT" in other["positions"]
    assert all(e["symbol"] != "ETHUSDT" for e in other["events"])
    assert (other["curve"].gross_exposure <= other["curve"].equity * c["gross"] + 1e-8).all()
