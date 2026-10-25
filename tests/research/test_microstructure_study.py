"""Signal selection is causal; absent funding is unknown and no sample is no alpha."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.data import read, save, sha
from research.crypto.audit_microstructure_study import audit
from research.crypto.microstructure_features import FACTOR_NAMES
from research.crypto.microstructure_funding import funding_receipt
from research.crypto.microstructure_proposals import NAMES, conditions, proposals, rank_proposals
from research.crypto.microstructure_quotes import QuoteSeries
from research.crypto.microstructure_study import (
    FROZEN_SOURCES,
    admission,
    runtime_identity,
    screen_summary,
    study,
    verify_code_freeze,
)


def receipt(events=None):
    return {
        "schema": "crypto-quant-binance-funding-receipt.v1",
        "queries": [
            {
                "symbol": "BTCUSDT",
                "startTime": 1000,
                "endTime": 2000,
                "limit": 1000,
                "response_received_ms": 3000,
                "tool": "mcp__codex_apps__binance_get_futures_usds_funding_rate_history",
                "raw_result": {"isError": False, "structuredContent": {"result": events or []}},
            }
        ],
    }


def test_only_explicit_complete_successful_empty_query_means_no_funding_events():
    result = funding_receipt(receipt(), ["BTCUSDT"])
    assert result["BTCUSDT"]["coverage"]["complete"] and result["BTCUSDT"]["events"] == []
    with pytest.raises(ValueError, match="Missing explicit"):
        funding_receipt(receipt(), ["BTCUSDT", "ETHUSDT"])


@pytest.mark.parametrize("fault", ["failed", "future", "truncated", "unknown", "fractional_clock"])
def test_failed_or_incomplete_funding_is_not_zero(fault):
    data = receipt()
    query = data["queries"][0]
    if fault == "failed":
        query["raw_result"]["isError"] = True
    elif fault == "future":
        query["response_received_ms"] = 1999
    elif fault == "truncated":
        query["limit"] = 1
        query["raw_result"]["structuredContent"]["result"] = [{}]
    elif fault == "fractional_clock":
        query["startTime"] = 1000.5
    else:
        query["raw_result"]["structuredContent"] = {"error": "unknown"}
    with pytest.raises(ValueError):
        funding_receipt(data, ["BTCUSDT"])


def test_actual_raw_funding_milliseconds_are_not_rounded_or_coalesced():
    event = {
        "symbol": "BTCUSDT",
        "fundingTime": 1501,
        "fundingRate": "0.0001",
        "rateType": "Regular",
    }
    result = funding_receipt(receipt([event]), ["BTCUSDT"])
    assert result["BTCUSDT"]["events"] == [{"time_ms": 1501, "rate": 0.0001}]
    with pytest.raises(ValueError, match="Duplicate"):
        funding_receipt(receipt([event, event]), ["BTCUSDT"])


def signal_row(**overrides):
    return SimpleNamespace(
        **{
            "received_flow_30s": 0.6,
            "received_flow_120s": 0.3,
            "persistence_30s": 1.0,
            "imbalance_l5": 0.4,
            "microprice_deviation_bps": 0.2,
            **overrides,
        }
    )


def test_book_continuation_and_absorption_directions_follow_frozen_rules():
    assert conditions(signal_row(), 0) == [(NAMES[0], 1), (NAMES[1], 1)]
    reverse = signal_row(imbalance_l5=-0.4, microprice_deviation_bps=-0.2)
    assert conditions(reverse, 0) == [(NAMES[0], 1), (NAMES[2], -1)]
    assert conditions(reverse, 6) == [(NAMES[0], 1)]
    assert conditions(signal_row(received_flow_30s=0.49), 0) == []


def test_proposal_ranking_ignores_outcomes_and_breaks_equal_scores_by_symbol():
    raw = pd.DataFrame(
        {
            "candidate": [NAMES[0]] * 3,
            "decision_wall_ns": [60] * 3,
            "score": [0.2, 0.2, 0.1],
            "symbol": ["ETHUSDT", "BTCUSDT", "BNBUSDT"],
            "outcome": [1000, -1000, 10000],
        }
    )
    assert rank_proposals(raw).symbol.tolist() == ["BTCUSDT"]
    raw["outcome"] *= -1
    assert rank_proposals(raw.sample(frac=1, random_state=1)).symbol.tolist() == ["BTCUSDT"]


def test_empty_and_source_failed_screens_never_pass():
    rule = {"minimum_mature_signals_each_candidate": 30, "minimum_positive_net_label_fraction": 0.8}
    empty = screen_summary([], [0, 3, 10])
    assert set(empty) == set(NAMES)
    assert admission(empty, rule, True) == []
    labels = [
        {
            "candidate": NAMES[0],
            "known": True,
            "reason": "take",
            "net_labels": {"0": 0.01, "3": 0.01, "10": -0.001},
        }
    ] * 30
    assert admission(screen_summary(labels, [0, 3, 10]), rule, True) == []
    positive = deepcopy(labels)
    for item in positive:
        item["net_labels"]["10"] = 0.001
    assert admission(screen_summary(positive, [0, 3, 10]), rule, False) == []
    assert admission(screen_summary(positive, [0, 3, 10]), rule, True) == [NAMES[0]]


def test_study_cannot_start_labels_until_both_enrolled_segments_exist(tmp_path):
    plan = Path("research/crypto/microstructure_economic_plan_v001.json").absolute()
    enrollment = tmp_path / "enrollment.json"
    save(enrollment, {"economic_plan_sha256": sha(plan), "discovery_segments": ["first", "second"]})
    output = tmp_path / "should-not-exist"
    with pytest.raises(ValueError, match="Both enrolled"):
        study(
            plan,
            enrollment,
            [],
            tmp_path / "no-funding-yet.json",
            output,
            tmp_path / "no-freeze-yet.json",
        )
    assert not output.exists()


def test_future_quote_flow_and_clock_rows_cannot_change_previous_minute_proposals():
    plan = read(Path("research/crypto/microstructure_economic_plan_v001.json"))
    plan["symbols"] = ["BTCUSDT"]
    features = pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "decision_wall_ns": np.arange(181) * 1_000_000_000,
            "decision_monotonic_ns": np.arange(181) * 1_000_000_000,
            "complete_factors": True,
        }
    )

    for name in FACTOR_NAMES:
        features[name] = 0.1
    features[
        [
            "received_flow_30s",
            "received_flow_120s",
            "persistence_30s",
            "imbalance_l5",
            "microprice_deviation_bps",
        ]
    ] = [0.6, 0.3, 1.0, 0.4, 0.2]
    time = np.arange(1801, dtype=np.int64) * 100_000_000
    raw_quotes = pd.DataFrame(
        {
            "mono_ns": time,
            "wall_ns": time,
            "bid": 100.0,
            "ask": 100.01,
            "valid": True,
            "epoch": 0,
            "event_ms": time // 1_000_000,
            "server_lower_ms": time / 1e6,
            "server_upper_ms": time / 1e6,
            "age_upper_ms": 1.0,
        }
    )
    trades = pd.DataFrame({"mono_ns": time, "valid": True})
    original, _ = proposals(
        features, {"BTCUSDT": QuoteSeries(raw_quotes)}, {"BTCUSDT": trades}, plan
    )
    altered = features.copy()
    altered.loc[altered.decision_wall_ns.gt(60_000_000_000), "received_flow_30s"] = -0.9
    raw_quotes.loc[raw_quotes.mono_ns.gt(60_000_000_000), ["bid", "ask"]] *= 10
    trades.loc[trades.mono_ns.gt(60_000_000_000), "valid"] = False
    changed, _ = proposals(altered, {"BTCUSDT": QuoteSeries(raw_quotes)}, {"BTCUSDT": trades}, plan)
    pd.testing.assert_frame_equal(
        original.loc[original.decision_wall_ns.le(60_000_000_000)].reset_index(drop=True),
        changed.loc[changed.decision_wall_ns.le(60_000_000_000)].reset_index(drop=True),
    )


def test_full_two_segment_pipeline_keeps_spread_and_fees_in_a_flat_market(tmp_path):
    plan = deepcopy(read(Path("research/crypto/microstructure_economic_plan_v001.json")))
    plan["symbols"] = ["BTCUSDT"]
    plan["quote_labels"]["maximum_holding_seconds"] = 6
    plan_path = tmp_path / "synthetic-plan.json"
    save(plan_path, plan)
    enrollment = tmp_path / "synthetic-enrollment.json"
    save(
        enrollment,
        {"economic_plan_sha256": sha(plan_path), "discovery_segments": ["first", "second"]},
    )
    frozen = {
        "plan_sha256": sha(plan_path),
        "enrollment_sha256": sha(enrollment),
        "source_sha256": {name: sha(Path("research/crypto") / name) for name in FROZEN_SOURCES},
        "runtime": runtime_identity(),
    }
    code_freeze = tmp_path / "synthetic-code-freeze.json"
    save(code_freeze, frozen)
    segments = []
    base = 1_700_000_040_000_000_000
    time = np.arange(241, dtype=np.int64) * 1_000_000_000
    for number, name in enumerate(["first", "second"]):
        folder = tmp_path / f"normalized-{name}"
        folder.mkdir()
        wall = base + number * 600_000_000_000 + time
        quotes = pd.DataFrame(
            {
                "mono_ns": time,
                "wall_ns": wall,
                "bid": 100.0,
                "ask": 100.01,
                "bid_text": "100.0",
                "ask_text": "100.01",
                "valid": True,
                "epoch": 0,
                "event_ms": wall // 1_000_000 - 1,
                "server_lower_ms": wall / 1e6,
                "server_upper_ms": wall / 1e6,
                "age_upper_ms": 1.0,
            }
        )
        quotes.to_parquet(folder / "BTCUSDT_quotes.parquet", index=False)
        pd.DataFrame({"mono_ns": time, "valid": True}).to_parquet(
            folder / "BTCUSDT_trade_clock.parquet", index=False
        )
        features = pd.DataFrame(
            {
                "symbol": "BTCUSDT",
                "decision_wall_ns": wall,
                "decision_monotonic_ns": time,
                "complete_factors": True,
            }
        )
        for factor in FACTOR_NAMES:
            features[factor] = 0.1
        features[
            [
                "received_flow_30s",
                "received_flow_120s",
                "persistence_30s",
                "imbalance_l5",
                "microprice_deviation_bps",
            ]
        ] = [0.6, 0.3, 1.0, 0.4, 0.2]
        features.to_parquet(folder / "features.parquet", index=False)
        save(
            folder / "result.json",
            {
                "source_capture": name,
                "source_engineering_passed": True,
                "capture_end_monotonic_ns": 241_000_000_000,
            },
        )
        save(
            folder / "input_freeze.json",
            {
                "economic_plan_sha256": sha(plan_path),
                "sources": {
                    name: frozen["source_sha256"][name]
                    for name in [
                        "prepare_microstructure_quotes.py",
                        "microstructure_quotes.py",
                        "microstructure_features.py",
                    ]
                },
            },
        )
        save(folder / "receipts.json", {p.name: sha(p) for p in folder.iterdir()})
        segments.append(folder)
    funding = receipt()
    query = funding["queries"][0]
    query.update(
        startTime=int(base // 1_000_000 - 1000),
        endTime=int((base + 900_000_000_000) // 1_000_000),
        response_received_ms=int((base + 901_000_000_000) // 1_000_000),
    )
    funding_path = tmp_path / "synthetic-funding.json"
    save(funding_path, funding)
    output = tmp_path / "synthetic-study"
    study(plan_path, enrollment, segments, funding_path, output, code_freeze)
    result = read(output / "screen.json")
    assert result["candidates"][NAMES[0]]["known_labels"] == 8
    assert result["candidates"][NAMES[1]]["known_labels"] == 8
    assert all(
        cost["mean_unit_net_label"] < 0 for cost in result["candidates"][NAMES[0]]["costs"].values()
    )
    assert read(output / "admission.json")["quote_label_screen_eligible"] == []
    independent = tmp_path / "synthetic-audit.json"
    audit(output, segments, independent)
    assert read(independent)["net_label_arithmetic_checks"] == 48
    assert read(independent)["maximum_net_label_error"] < 1e-10


@pytest.mark.parametrize("fault", ["source", "runtime"])
def test_changed_preregistered_implementation_or_runtime_is_rejected(tmp_path, fault):
    plan = Path("research/crypto/microstructure_economic_plan_v001.json")
    enrollment = tmp_path / "enrollment.json"
    save(enrollment, {"economic_plan_sha256": sha(plan)})
    frozen = {
        "plan_sha256": sha(plan),
        "enrollment_sha256": sha(enrollment),
        "source_sha256": {name: sha(Path("research/crypto") / name) for name in FROZEN_SOURCES},
        "runtime": runtime_identity(),
    }
    if fault == "source":
        frozen["source_sha256"]["microstructure_quotes.py"] = "0" * 64
    else:
        frozen["runtime"]["python"] = "changed"
    code_freeze = tmp_path / "code-freeze.json"
    save(code_freeze, frozen)
    with pytest.raises(ValueError, match="Frozen"):
        verify_code_freeze(code_freeze, plan, enrollment)
