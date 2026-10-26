"""Execution failures must not turn missing histories into profitable accounts."""

import json

import pandas as pd
import pytest

from research.crypto.calendar_portfolio import replay as dense_replay
from research.crypto.sparse_calendar import replay
from research.crypto.sparse_sources import UnknownHeldSource


def fixture():
    clock = pd.date_range("2024-12-31 23:00", "2025-01-02", freq="5min", inclusive="left", tz="UTC")
    bars = {
        s: pd.DataFrame(
            {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "quote_volume": 1e8,
                "price_usable": True,
                "execution_liquidity_usable": True,
            },
            index=clock,
        )
        for s in ["X", "Y"]
    }
    funding = {
        s: pd.DataFrame(columns=["funding_rate", "mark"], index=pd.DatetimeIndex([], tz="UTC"))
        for s in bars
    }
    hours = pd.date_range("2025-01-01", "2025-01-02", freq="h", inclusive="left", tz="UTC")
    scores = pd.DataFrame(
        {"target_X": 0.5, "target_Y": 0, "risk_scale": 1.0, "rebalance": False}, index=hours
    )
    scores.loc[hours[0], "rebalance"] = True
    config = {
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
    return bars, funding, scores, config


def directive(
    known="2025-01-01T00:00:00Z",
    cut="2025-01-01T01:00:00Z",
    terminal="2025-01-01T01:30:00Z",
    episode="X:observed_initial",
):
    return {
        "known_at": known,
        "entry_stop_at": cut,
        "terminal_at": terminal,
        "episode_key": episode,
        "blocked": False,
    }


def test_sparse_matches_dense_cash_fees_funding_and_confirmed_reversals():
    b, f, s, c = fixture()
    s.loc["2025-01-01T01:00Z":, "target_X"] = -0.5
    s.loc["2025-01-01T01:00Z", "rebalance"] = True
    f["X"] = pd.DataFrame(
        {"funding_rate": [0.002, -0.001], "mark": [110, 90]},
        index=pd.to_datetime(
            ["2025-01-01T00:05:00.015Z", "2025-01-01T01:05Z"], format="mixed", utc=True
        ),
    )
    a, d = [fn(b, f, s, c, "2025-01-01", "2025-01-01 02:00") for fn in [replay, dense_replay]]
    pd.testing.assert_frame_equal(a["curve"], d["curve"])
    assert (
        a["events"] == d["events"] and a["trades"] == d["trades"] and a["requests"] == d["requests"]
    )


def test_unlisted_flat_name_never_needs_a_fake_previous_price():
    b, f, s, c = fixture()
    b["Y"] = b["Y"].iloc[:0]
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00")
    assert set(a["positions"]) == {"X"} and "Y" not in a["resume_state"]["previous_prices"]


def test_missing_held_bar_aborts_instead_of_forward_filling_pnl():
    b, f, s, c = fixture()
    b["X"] = b["X"].drop(pd.Timestamp("2025-01-01T00:10Z"))
    with pytest.raises(UnknownHeldSource, match="actual held"):
        replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")


def test_missing_flat_target_or_capacity_warmup_cannot_create_a_fill():
    b, f, s, c = fixture()
    b["X"] = b["X"].drop(pd.Timestamp("2024-12-31T23:30Z"))
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")
    assert not a["events"] and not a["positions"] and a["cash"] == 10000


def test_invalid_execution_flag_blocks_orders_despite_positive_quote():
    b, f, s, c = fixture()
    b["X"].loc["2025-01-01T00:00Z", "execution_liquidity_usable"] = False
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")
    assert not a["events"] and a["capacity"]["unavailable_entry_requests"] >= 1


def test_mandatory_cutoff_exits_before_unknown_terminal():
    b, f, s, c = fixture()
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00", lifecycle={"X": [directive()]})
    exits = [e for e in a["events"] if e["type"] == "exit"]
    assert exits[0]["submitted_at"] == "2025-01-01T01:00:00+00:00"
    assert exits[0]["time"] == "2025-01-01T01:05:00+00:00" and exits[0]["reason"] == "contract_exit"
    assert not a["positions"] and len(a["trades"]) == 1


def test_zero_volume_cannot_be_used_as_a_terminal_cash_fill():
    b, f, s, c = fixture()
    b["X"].loc["2025-01-01T01:00Z":, "quote_volume"] = 0
    with pytest.raises(UnknownHeldSource, match="terminal cash"):
        replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00", lifecycle={"X": [directive()]})


def test_a_fill_exactly_at_terminal_is_not_an_automatic_settlement_proxy():
    b, f, s, c = fixture()
    with pytest.raises(UnknownHeldSource, match="before an archive fill"):
        replay(
            b,
            f,
            s,
            c,
            "2025-01-01",
            "2025-01-01 02:00",
            lifecycle={"X": [directive(cut="2025-01-01T01:25:00Z")]},
        )


def test_later_notice_cannot_change_earlier_orders_or_equity():
    b, f, s, c = fixture()
    a = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")
    changed = replay(
        b,
        f,
        s,
        c,
        "2025-01-01",
        "2025-01-01 01:00",
        lifecycle={
            "X": [
                directive(
                    known="2025-01-01T02:00Z", cut="2025-01-02T01:00Z", terminal="2025-01-02T01:30Z"
                )
            ]
        },
    )
    assert a["events"] == changed["events"]
    pd.testing.assert_frame_equal(a["curve"], changed["curve"])


def test_reused_ticker_never_transfers_old_inventory():
    b, f, s, c = fixture()
    with pytest.raises(UnknownHeldSource, match="different contract identity"):
        replay(
            b,
            f,
            s,
            c,
            "2025-01-01",
            "2025-01-01 02:00",
            lifecycle={
                "X": [
                    directive(
                        known="2025-01-01T01:00Z", cut=None, terminal=None, episode="X:new_asset"
                    )
                ]
            },
        )


def test_unfilled_intent_cannot_change_asset_during_its_execution_interval():
    b, f, s, c = fixture()
    a = replay(
        b,
        f,
        s,
        c,
        "2025-01-01",
        "2025-01-01 01:00",
        lifecycle={
            "X": [
                directive(known="2025-01-01T00:02Z", cut=None, terminal=None, episode="X:new_asset")
            ]
        },
    )
    assert a["requests"] and not a["events"] and not a["positions"]
    assert a["cash"] == 10000 and a["capacity"]["unavailable_entry_requests"] == 1


def test_unknown_held_funding_mark_aborts_but_unheld_event_is_not_cash():
    b, f, s, c = fixture()
    bad = pd.DataFrame(
        {"funding_rate": [0.002], "mark": [0]},
        index=pd.to_datetime(["2025-01-01T00:10Z"], utc=True),
    )
    f["Y"] = bad
    assert replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")["positions"]
    f["X"] = bad
    with pytest.raises(ValueError, match="unknown mark"):
        replay(b, f, s, c, "2025-01-01", "2025-01-01 01:00")


def test_pending_contract_exit_and_risk_state_survive_strict_json_restore():
    b, f, s, c = fixture()
    policy = {"X": [directive()]}
    b["X"].loc["2025-01-01T01:00Z", "quote_volume"] = 1
    full = replay(b, f, s, c, "2025-01-01", "2025-01-01 02:00", lifecycle=policy)
    first = replay(b, f, s, c, "2025-01-01", "2025-01-01 01:05", lifecycle=policy)
    state = json.loads(json.dumps(first["resume_state"], allow_nan=False))
    assert state["pending_exits"]["X"]["reason"] == "contract_exit" and state["positions"]
    second = replay(b, f, s, c, "2025-01-01 01:05", "2025-01-01 02:00", state, lifecycle=policy)
    pd.testing.assert_frame_equal(
        pd.concat([first["curve"], second["curve"].iloc[1:]], ignore_index=True), full["curve"]
    )
    assert (
        first["events"] + second["events"] == full["events"]
        and second["resume_state"] == full["resume_state"]
    )
