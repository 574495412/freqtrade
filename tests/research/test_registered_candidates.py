"""Causal funding clocks and economic admission invariants for registered candidates."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from freqtrade.research.data import read
from research.crypto.prepare_nfi_universe import funding_frame
from research.crypto.run_candidate import economic_audit
from research.crypto.strategies.funding_trend import carry_features


def load_factor():
    plan = read(Path(__file__).resolve().parents[2] / "research/crypto/funding_plan.json")
    spec = importlib.util.spec_from_file_location(
        "source_funding_test", plan["settings"]["research_factor_path"]
    )
    factor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factor)
    return factor


def test_completed_bar_must_not_see_funding_just_paid_at_its_close():
    frame = pd.DataFrame({"date": pd.date_range("2023-12-03T15:00", periods=2, freq="h", tz="UTC")})
    events = pd.DataFrame(
        {
            "original_date": pd.date_range("2023-12-01", periods=12, freq="8h", tz="UTC"),
            "funding_rate": 0.001,
        }
    )
    altered = events.copy()
    altered.loc[altered.original_date.eq(pd.Timestamp("2023-12-03T16:00Z")), "funding_rate"] = 100
    first = carry_features(frame, events, load_factor(), "2023-12-01", "2023-12-05")
    second = carry_features(frame, altered, load_factor(), "2023-12-01", "2023-12-05")
    assert first.funding_carry_24h.iloc[0] == pytest.approx(-0.003)
    assert first.funding_carry_24h.iloc[0] == second.funding_carry_24h.iloc[0]
    assert first.funding_carry_24h.iloc[1] != second.funding_carry_24h.iloc[1]


def test_visibility_delay_keeps_original_millisecond_precision():
    frame = pd.DataFrame({"date": [pd.Timestamp("2023-12-03T15:00Z")]})
    known = pd.DataFrame(
        {"original_date": [pd.Timestamp("2023-12-03T08:00Z")], "funding_rate": [0.001]}
    )
    future = pd.DataFrame(
        {"original_date": [pd.Timestamp("2023-12-03T15:55:00.001Z")], "funding_rate": [99.0]}
    )
    result = carry_features(
        frame, pd.concat([known, future]), load_factor(), "2023-12-01", "2023-12-05"
    )
    assert result.funding_carry_24h.iloc[0] == pytest.approx(-0.001)


def test_nonfinite_economics_cannot_be_admitted():
    stats = {
        "profit_total": 0.1,
        "profit_total_abs": 100.0,
        "max_drawdown_account": 0.01,
        "total_trades": 31,
        "trades": [{"profit_abs": 100.0}],
    }
    plan = {
        "screening_targets": {
            "minimum_closed_trades_per_evaluation": 30,
            "minimum_profit_factor": 1.1,
            "maximum_observed_drawdown": 0.15,
        }
    }
    assert economic_audit(stats, plan, 0.02)["passed"]
    with pytest.raises(ValueError, match="Nonfinite"):
        economic_audit(stats, plan, float("nan"))
    with pytest.raises(ValueError, match="did not reconcile"):
        economic_audit(dict(stats, profit_total_abs=99.0), plan, 0.02)


def test_funding_archives_must_match_their_published_settlement_clock(tmp_path):
    from zipfile import ZipFile

    times = pd.to_datetime(
        ["2024-01-01T00:00Z", "2024-01-01T08:00Z", "2024-01-01T16:00Z", "2024-01-01T20:00Z"]
    )
    frame = pd.DataFrame(
        {
            "calc_time": times.as_unit("ms").asi8,
            "funding_interval_hours": [8, 8, 8, 4],
            "last_funding_rate": [0.001] * 4,
        }
    )
    path = tmp_path / "funding.zip"

    def write(data):
        with ZipFile(path, "w") as archive:
            archive.writestr("funding.csv", data.to_csv(index=False))

    write(frame)
    records = [{"path": str(path)}]
    assert len(funding_frame(records, "2024-01-01", "2024-01-02")) == 4
    write(frame.drop(index=1))
    with pytest.raises(ValueError, match="gaps"):
        funding_frame(records, "2024-01-01", "2024-01-02")
    write(frame.assign(last_funding_rate=float("nan")))
    with pytest.raises(ValueError, match="rates"):
        funding_frame(records, "2024-01-01", "2024-01-02")


def test_irregular_funding_clock_requires_exact_public_api_agreement(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zipfile import ZipFile

    import research.crypto.prepare_nfi_universe as preparing

    clocks = pd.to_datetime(["2024-01-01T00:00Z", "2024-01-01T16:00Z"])
    frame = pd.DataFrame(
        {
            "calc_time": clocks.as_unit("ms").asi8,
            "funding_interval_hours": [8, 8],
            "last_funding_rate": [0.001, 0.002],
        }
    )
    archive = tmp_path / "funds.zip"
    with ZipFile(archive, "w") as z:
        z.writestr("funding.csv", frame.to_csv(index=False))
    body = [
        {"symbol": "TESTUSDT", "fundingTime": int(t.value // 1_000_000), "fundingRate": str(rate)}
        for t, rate in zip(clocks, [0.001, 0.002], strict=True)
    ]
    monkeypatch.setattr(
        preparing.requests,
        "get",
        lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: body),
    )
    result = preparing.funding_frame(
        [{"path": str(archive)}],
        "2024-01-01",
        "2024-01-02",
        symbol="TESTUSDT",
        reconciliation_dir=tmp_path / "verified",
    )
    assert len(result.attrs["api_verified_irregular_windows"]) == 1
    body.insert(
        1,
        {
            "symbol": "TESTUSDT",
            "fundingTime": int(pd.Timestamp("2024-01-01T08:00Z").value // 1_000_000),
            "fundingRate": "0.003",
        },
    )
    with pytest.raises(ValueError, match="calendars disagree"):
        preparing.funding_frame(
            [{"path": str(archive)}],
            "2024-01-01",
            "2024-01-02",
            symbol="TESTUSDT",
            reconciliation_dir=tmp_path / "disagree",
        )
