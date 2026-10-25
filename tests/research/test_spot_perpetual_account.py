"""Cash conservation, exact settlements and non-atomic hedge failure cases."""

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


spec = importlib.util.spec_from_file_location(
    "spot_account", Path(__file__).parents[2] / "research/crypto/spot_perpetual_account.py"
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def fixture():
    index = pd.date_range(
        "2024-12-31 23:55", "2025-01-01 02:00", freq="5min", inclusive="left", tz="UTC"
    )
    bars = {
        "X": pd.DataFrame(
            {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "quote_volume": 100000000.0,
            },
            index=index,
        )
    }
    spot = {s: f.copy() for s, f in bars.items()}
    marks = {s: f[["open", "high", "low", "close"]].copy() for s, f in bars.items()}
    funding = {
        "X": pd.DataFrame(columns=["funding_rate", "mark"], index=pd.DatetimeIndex([], tz="UTC"))
    }
    signals = [
        {"time": "2025-01-01T00:00:00+00:00", "assets": {"X": {"entry": True, "exit": False}}}
    ]
    config = {
        "capital": 10000,
        "spot_fee_bps": 10,
        "perpetual_fee_bps": 5,
        "slippage_bps": 10,
        "entry_slippage_buffer_bps": 10,
        "initial_futures_wallet_fraction": 0.7,
        "asset_spot_fraction": 0.25,
        "initial_gross_target": 0.5,
        "gross_reduction_trigger": 0.8,
        "gross_reduction_target": 0.5,
        "participation": 0.01,
        "drawdown_halt": 0.15,
        "initial_margin_ratio": 1.0,
        "maintenance_stress_ratio": 0.05,
        "margin_exit_ratio": 0.2,
        "rearm_hours": 24,
    }
    return spot, bars, marks, funding, signals, config


def run(parts, end="2025-01-01 00:20", **kwargs):
    return engine.replay(*parts, "2025-01-01", end, **kwargs)


def test_two_wallets_spot_principal_and_all_four_fees_reconcile():
    parts = fixture()
    result = run(parts)
    entries = [e for e in result["events"] if e["type"] == "entry"]
    q = entries[0]["quantity"]
    assert entries[0]["gross_quantity"] == pytest.approx(q / 0.999)
    first = result["curve"].iloc[1]
    assert first.futures_wallet == pytest.approx(7000 - q * 99.9 * 0.0005)
    assert first.quote_cash == pytest.approx(3000 - q / 0.999 * 100.1)
    expected_pnl = q * (99.9 * 0.999 - 100.1 / 0.999 + 99.9 - 100.1 - 0.0005 * (99.9 + 100.1))
    assert result["curve"].iloc[-1].equity == pytest.approx(10000 + expected_pnl)
    assert len(result["trades"]) == 1
    assert result["trades"][0]["net_pnl"] == pytest.approx(expected_pnl)
    assert result["positions"] == {}
    assert sum(result["wallets"].values()) == pytest.approx(10000 + expected_pnl)


def test_exact_funding_mark_and_entry_exit_boundaries_use_prior_inventory():
    parts = list(fixture())
    times = pd.to_datetime(
        [
            "2025-01-01T00:00:00.015Z",
            "2025-01-01T00:05:00Z",
            "2025-01-01T00:05:00.015Z",
            "2025-01-01T00:20:00Z",
        ],
        utc=True,
        format="mixed",
    )
    parts[3]["X"] = pd.DataFrame(
        {"funding_rate": [0.1, 0.1, 0.001, 0.002], "mark": [999, 999, 150, 125]}, index=times
    )
    result = run(parts)
    entries = [e for e in result["events"] if e["type"] == "entry"]
    paid = [e for e in result["events"] if e["type"] == "funding"]
    assert len(paid) == 2
    assert paid[0]["time"] == "2025-01-01T00:05:00.015000+00:00"
    assert sum(e["cash_delta"] for e in paid) == pytest.approx(entries[0]["quantity"] * 0.4)
    assert paid[-1]["time"] == result["trades"][0]["exit_time"]


def test_missing_short_fill_unwinds_actual_spot_with_costs_next_interval():
    parts = fixture()
    parts[1]["X"].loc[pd.Timestamp("2025-01-01", tz="UTC"), "quote_volume"] = 0
    result = run(parts)
    assert result["capacity"]["hedge_failures"] == 1
    assert len(result["trades"]) == 1 and not result["trades"][0]["matched_hedge"]
    assert result["trades"][0]["net_pnl"] < 0
    assert result["trades"][0]["exit_time"] == "2025-01-01T00:10:00+00:00"
    assert not any(e["type"] == "entry" and e["market"] == "perp" for e in result["events"])
    assert result["wallets"]["futures"] == 7000


def test_hedged_total_nav_cannot_hide_unfunded_futures_margin_path():
    parts = fixture()
    shock = pd.Timestamp("2025-01-01 00:05", tz="UTC")
    parts[0]["X"].loc[shock:, ["open", "high", "low", "close"]] = 300
    parts[1]["X"].loc[shock:, ["open", "high", "low", "close"]] = 300
    parts[2]["X"].loc[shock:, ["open", "high", "low", "close"]] = 300
    parts[2]["X"].loc[shock, "high"] = 600
    result = run(parts)
    assert result["flags"]["possible_liquidation"] is True
    assert result["risk_halted"] is True
    assert result["curve"].iloc[2].equity > 9800
    transfers = [e for e in result["events"] if e["type"] == "transfer"]
    assert len(transfers) == 1 and transfers[0]["amount"] == 7000
    exits = [e for e in result["events"] if e["type"] == "exit"]
    assert all(e["time"] > "2025-01-01T00:10:00+00:00" for e in exits)


def test_actual_json_resume_preserves_nonflat_inventory_and_pending_unwind():
    parts = fixture()
    parts[1]["X"].loc[pd.Timestamp("2025-01-01", tz="UTC"), "quote_volume"] = 0
    full = run(parts)
    first = run(parts, end="2025-01-01 00:05", final_end="2025-01-01 00:20")
    restored = json.loads(json.dumps(first["resume_state"]))
    assert restored["positions"]["X"]["spot_q"] > 0
    assert restored["positions"]["X"]["exit_request"]["reason"] == "entry_hedge_failure"
    second = engine.replay(
        *parts,
        "2025-01-01 00:05",
        "2025-01-01 00:20",
        initial_state=restored,
        final_end="2025-01-01 00:20",
    )
    stitched = pd.concat([first["curve"], second["curve"].iloc[1:]], ignore_index=True)
    pd.testing.assert_frame_equal(stitched, full["curve"])
    assert first["events"] + second["events"] == full["events"]
    assert first["trades"] + second["trades"] == full["trades"]
    assert second["resume_state"] == full["resume_state"]


def test_gross_reduction_keeps_one_campaign_and_delays_fills():
    parts = fixture()
    shock = pd.Timestamp("2025-01-01 00:05", tz="UTC")
    for source in parts[:3]:
        source["X"].loc[shock:, ["open", "high", "low", "close"]] = 170
    result = run(parts)
    reductions = [e for e in result["events"] if e.get("reason") == "gross_drift_reduction"]
    assert len(reductions) == 2
    assert all(e["time"] == "2025-01-01T00:15:00+00:00" for e in reductions)
    assert len(result["trades"]) == 1 and result["capacity"]["hedge_failures"] == 0
