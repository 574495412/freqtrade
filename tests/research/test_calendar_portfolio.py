"""Weekly ownership, millisecond settlement, no future cash and confirmed flips."""

import json

import pandas as pd
import pytest

from research.crypto.calendar_portfolio import replay


def fixture():
    index = pd.date_range("2024-12-31 23:00", "2025-01-03", freq="5min", inclusive="left", tz="UTC")
    bars = {
        s: pd.DataFrame(
            {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "quote_volume": 1e8},
            index=index,
        )
        for s in ["X", "Y"]
    }
    funding = {
        s: pd.DataFrame(columns=["funding_rate", "mark"], index=pd.DatetimeIndex([], tz="UTC"))
        for s in bars
    }
    hours = pd.date_range("2025-01-01", "2025-01-03", freq="h", inclusive="left", tz="UTC")
    scores = pd.DataFrame(
        {"target_X": 0.5, "target_Y": 0.0, "risk_scale": 1.0, "rebalance": False}, index=hours
    )
    scores.loc[hours[0], "rebalance"] = True
    c = {
        "capital": 10000,
        "fee_bps": 5,
        "slippage_bps": 3,
        "participation": 0.01,
        "gross": 0.8,
        "max_positions": 10,
        "rebalance_hours": 24,
        "threshold": 0.0032,
        "stop_loss": 0.08,
        "drawdown_halt": 0.15,
    }
    return bars, funding, scores, c


def test_weekly_flag_does_not_resubmit_or_resize_at_each_midnight():
    b, f, s, c = fixture()
    a = replay(b, f, s, c, "2025-01-01", "2025-01-03")
    assert len(a["requests"]) == 1
    assert len([e for e in a["events"] if e["type"] == "entry"]) == 1


def test_exact_settlement_before_entry_tie_belongs_to_prior_inventory():
    b, f, s, c = fixture()
    f["X"] = pd.DataFrame(
        {"funding_rate": [0.1, 0.1, 0.001, 0.002], "mark": [999, 999, 150, 125]},
        index=pd.to_datetime(
            [
                "2025-01-01T00:00:00.015Z",
                "2025-01-01T00:05Z",
                "2025-01-01T00:05:00.015Z",
                "2025-01-01T00:20Z",
            ],
            format="mixed",
            utc=True,
        ),
    )
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 00:20")
    paid = [e for e in a["events"] if e["type"] == "funding"]
    q = a["positions"]["X"]["q"]
    assert len(paid) == 2 and paid[0]["time"] == "2025-01-01T00:05:00.015000+00:00"
    assert sum(e["cash_delta"] for e in paid) == pytest.approx(-q * 0.4)
    assert paid[-1]["effective_time"] == "2025-01-01T00:20:00+00:00"


def test_unknown_inbar_funding_does_not_change_prebar_request_quantity():
    b, f, s, c = fixture()
    s.loc["2025-01-01T01:00Z", "rebalance"] = True
    s.loc["2025-01-01T01:00Z":, "target_Y"] = 0.5
    baseline = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00")
    f["X"] = pd.DataFrame(
        {"funding_rate": [-0.01], "mark": [100.0]},
        index=pd.to_datetime(["2025-01-01T01:00:00.015Z"], utc=True),
    )
    changed = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00")
    before = [r for r in baseline["requests"] if r["time"] == "2025-01-01T01:00:00+00:00"]
    after = [r for r in changed["requests"] if r["time"] == "2025-01-01T01:00:00+00:00"]
    assert before == after and before


def test_reversal_waits_for_confirmed_close_then_next_interval_and_resumes_json():
    b, f, s, c = fixture()
    s.loc["2025-01-01T01:00Z":, "target_X"] = -0.5
    s.loc["2025-01-01T01:00Z", "rebalance"] = True
    full = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00")
    exits = [e for e in full["events"] if e["type"] == "exit"]
    shorts = [e for e in full["events"] if e["type"] == "entry" and e["side"] == -1]
    assert exits[0]["time"] == "2025-01-01T01:05:00+00:00"
    assert shorts[0]["time"] == "2025-01-01T01:10:00+00:00"
    first = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:05")
    state = json.loads(json.dumps(first["resume_state"]))
    assert state["deferred_reversals"]["X"]["ready_at"]
    second = replay(b, f, s, c, "2025-01-01 01:05", "2025-01-01 02:00", state)
    pd.testing.assert_frame_equal(
        pd.concat([first["curve"], second["curve"].iloc[1:]], ignore_index=True), full["curve"]
    )
    assert first["events"] + second["events"] == full["events"]
    assert second["resume_state"] == full["resume_state"]


def test_partial_exit_never_grants_instant_opposite_inventory():
    b, f, s, c = fixture()
    s.loc["2025-01-01T01:00Z":, "target_X"] = -0.5
    s.loc["2025-01-01T01:00Z", "rebalance"] = True
    b["X"].loc["2025-01-01T01:00Z":"2025-01-01T01:05Z", "quote_volume"] = 0
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:10")
    assert a["capacity"]["partial_exit_intervals"] >= 0
    assert a["positions"]["X"]["side"] == 1
    assert not any(e["type"] == "entry" and e["side"] == -1 for e in a["events"])
