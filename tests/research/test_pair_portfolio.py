"""Economic failure cases, campaign counting and actual JSON continuation."""

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


spec = importlib.util.spec_from_file_location(
    "pairs", Path(__file__).parents[2] / "research/crypto/pair_portfolio.py"
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def fixture():
    index = pd.date_range("2024-12-31 23:00", "2025-01-02", freq="5min", inclusive="left", tz="UTC")
    bars = {
        s: pd.DataFrame(
            {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "quote_volume": 100_000_000.0,
            },
            index=index,
        )
        for s in ("X", "Y")
    }
    rates = {
        s: pd.DataFrame(columns=["funding_rate", "mark"], index=pd.DatetimeIndex([], tz="UTC"))
        for s in bars
    }
    config = {
        "capital": 10000,
        "fee_bps": 5,
        "slippage_bps": 10,
        "participation": 0.01,
        "gross": 0.8,
        "stop_loss": 0.08,
        "drawdown_halt": 0.15,
        "decision_hours": 4,
        "maximum_hold_hours": 168,
        "rearm_hours": 24,
        "z_stop": 4,
        "z_exit": 0.5,
        "max_pairs": 5,
        "max_positions": 10,
    }
    clocks = pd.date_range("2025-01-01", "2025-01-02", freq="4h", inclusive="left", tz="UTC")
    signals = [
        {
            "time": t.isoformat(),
            "pairs": [
                {
                    "pair_key": "X/Y",
                    "x": "X",
                    "y": "Y",
                    "model_id": "fixed",
                    "beta": 1.0,
                    "z": 2.5 if i == 0 else 0.1,
                    "signal_valid": True,
                    "entry_eligible": i == 0,
                }
            ],
        }
        for i, t in enumerate(clocks)
    ]
    return bars, rates, signals, config


def run(bars, rates, signals, config, end="2025-01-01 08:00"):
    return engine.replay(bars, rates, signals, config, "2025-01-01", end)


def test_pair_costs_and_lifecycle_are_counted_once():
    a = run(*fixture())
    assert len(a["trades"]) == 1
    assert len([e for e in a["events"] if e["type"] == "entry"]) == 2
    assert len([e for e in a["events"] if e["type"] == "exit"]) == 2
    assert a["trades"][0]["net_pnl"] < 0
    assert a["curve"].equity.iloc[-1] - 10000 == pytest.approx(a["trades"][0]["net_pnl"])
    assert a["trades"][0]["hedge_matched"]
    # Initial actual entry also pays the exit reserve before taking gross risk.
    assert a["curve"].gross_exposure.iloc[1] / a["curve"].equity.iloc[1] <= 0.8 + 1e-12


def test_one_leg_fill_is_not_freely_cancelled_and_closes_next_interval():
    bars, rates, signals, config = fixture()
    bars["Y"].loc[pd.Timestamp("2025-01-01", tz="UTC"), "quote_volume"] = 0
    a = run(bars, rates, signals, config)
    assert a["capacity"]["hedge_failures"] == 1
    assert len(a["trades"]) == 1 and not a["trades"][0]["hedge_matched"]
    entries = [e for e in a["events"] if e["type"] == "entry"]
    exits = [e for e in a["events"] if e["type"] == "exit"]
    assert len(entries) == len(exits) == 1
    assert pd.Timestamp(exits[0]["time"]) - pd.Timestamp(entries[0]["time"]) == pd.Timedelta(
        minutes=5
    )
    assert a["trades"][0]["net_pnl"] < 0
    assert a["curve"].net_exposure.iloc[1] != 0


def test_partial_exit_owns_both_symbols_until_flat_and_json_resume():
    bars, rates, signals, config = fixture()
    for row in signals[1:]:
        row["pairs"][0].update(model_id="new", z=2.5, entry_eligible=True)
    bars["Y"].loc["2025-01-01 04:00":"2025-01-01 08:00", "quote_volume"] = 0
    full = run(bars, rates, signals, config, end="2025-01-01 12:00")
    first = run(bars, rates, signals, config, end="2025-01-01 06:00")
    assert len(first["positions"]) == 1 and len(first["pair_positions"]) == 1
    assert first["pending_exits"]
    state = json.loads(json.dumps(first["resume_state"], allow_nan=False))
    second = engine.replay(
        bars, rates, signals, config, "2025-01-01T06:00:00+00:00", "2025-01-01 12:00", state
    )
    pd.testing.assert_frame_equal(
        pd.concat([first["curve"], second["curve"].iloc[1:]], ignore_index=True), full["curve"]
    )
    for key in ("events", "requests", "trades"):
        assert first[key] + second[key] == full[key]
    assert second["resume_state"] == full["resume_state"]
    assert len(full["requests"]) == 1
    assert full["trades"][0]["reason"] == "formation_change"


def test_funding_and_one_leg_stop_close_the_pair():
    bars, rates, signals, config = fixture()
    for row in signals[1:]:
        row["pairs"][0].update(z=2.5, entry_eligible=True)
    rates["X"] = pd.DataFrame(
        {"funding_rate": [0.001], "mark": [100.0]},
        index=pd.DatetimeIndex(["2025-01-01 01:00"], tz="UTC"),
    )
    bars["X"].loc["2025-01-01 01:05", "low"] = 90
    a = run(bars, rates, signals, config)
    assert len(a["trades"]) == 1 and a["trades"][0]["reason"] == "leg_stop"
    funding = [e for e in a["events"] if e["type"] == "funding"]
    assert len(funding) == 1 and funding[0]["cash_delta"] < 0
    assert len([e for e in a["events"] if e["type"] == "exit"]) == 2


def test_halt_persists_across_calendar_and_no_future_bars_change_prefix():
    bars, rates, signals, config = fixture()
    config["drawdown_halt"] = 0.0001
    a = run(bars, rates, signals, config, end="2025-01-01 04:00")
    assert a["risk_halted"]
    state = json.loads(json.dumps(a["resume_state"]))
    resumed = engine.replay(
        bars, rates, signals, config, "2025-01-01 04:00", "2025-01-01 08:00", state
    )
    assert resumed["risk_halted"] and resumed["requests"] == []
    config["drawdown_halt"] = 0.15
    expected = run(bars, rates, signals, config, end="2025-01-01 04:00")
    for frame in bars.values():
        frame.loc["2025-01-01 04:00":, ["open", "high", "low", "close"]] *= 100
    changed = run(bars, rates, signals, config, end="2025-01-01 04:00")
    pd.testing.assert_frame_equal(expected["curve"], changed["curve"])
    assert expected["events"] == changed["events"]


def test_absent_signal_or_wrong_resume_clock_cannot_be_silent_cash():
    bars, rates, signals, config = fixture()
    with pytest.raises(ValueError, match="Missing timestamped"):
        run(bars, rates, signals[1:], config)
    first = run(bars, rates, signals, config, end="2025-01-01 04:00")
    with pytest.raises(ValueError, match="Resume boundary"):
        engine.replay(
            bars,
            rates,
            signals,
            config,
            "2025-01-01 05:00",
            "2025-01-01 08:00",
            first["resume_state"],
        )
