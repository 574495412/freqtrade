"""Executable quote sides, latency, source uncertainty and exact funding exposure."""

import numpy as np
import pandas as pd
import pytest

from research.crypto.audit_microstructure_study import scalar_path
from research.crypto.microstructure_quotes import (
    QuoteSeries,
    clocks_for_receipts,
    funding_cash,
    net_labels,
    quote_path,
)


SPEC = {"maximum_holding_seconds": 6, "take_price_fraction": 0.004, "stop_price_fraction": 0.004}


def frame():
    time = np.arange(20, dtype=np.int64) * 1_000_000_000
    return pd.DataFrame(
        {
            "mono_ns": time,
            "wall_ns": time + 1_000_000_000_000,
            "bid": 100.0,
            "ask": 100.01,
            "valid": True,
            "epoch": 0,
            "event_ms": time // 1_000_000 + 1_000_000 - 1,
            "server_lower_ms": time // 1_000_000 + 1_000_000,
            "server_upper_ms": time // 1_000_000 + 1_000_000,
            "age_upper_ms": 1.0,
        }
    )


@pytest.mark.parametrize("sign", [1, -1])
def test_flat_mid_loses_spread_and_pays_both_sides(sign):
    quotes = QuoteSeries(frame())
    path = quote_path(quotes, 0, sign, SPEC, 20_000_000_000)
    assert path["known"] and path["reason"] == "time"
    assert path["entry_monotonic_ns"] == 1_000_000_000
    assert path["exit_monotonic_ns"] == 7_000_000_000
    expected_entry, expected_exit = (100.01, 100) if sign == 1 else (100, 100.01)
    assert path["entry_price"] == expected_entry
    assert path["exit_price"] == expected_exit
    result = net_labels(
        path,
        {"known": True, "cash_per_base_unit": 0},
        {"base_fee": 0.0005, "extra_bps": [0, 3, 10]},
    )
    assert result["10"] < result["3"] < result["0"] < 0


def test_take_trigger_uses_later_quote_even_if_market_reverses_to_a_loss():
    raw = frame()
    raw.loc[3, ["bid", "ask"]] = [101.0, 101.01]
    raw.loc[4, ["bid", "ask"]] = [99.0, 99.01]
    path = quote_path(QuoteSeries(raw), 0, 1, SPEC, 20_000_000_000)
    assert path["reason"] == "take"
    assert path["trigger_monotonic_ns"] == 3_000_000_000
    assert path["exit_monotonic_ns"] == 4_000_000_000
    assert path["exit_price"] == 99.0
    assert path["gross_unit_return"] < 0


def test_adverse_gap_uses_the_quote_after_stop_latency():
    raw = frame()
    raw.loc[3, ["bid", "ask"]] = [99.5, 99.51]
    raw.loc[4, ["bid", "ask"]] = [97.0, 97.01]
    path = quote_path(QuoteSeries(raw), 0, 1, SPEC, 20_000_000_000)
    assert path["reason"] == "stop" and path["exit_price"] == 97


@pytest.mark.parametrize("fault", ["invalid", "epoch", "missing", "clock", "source_gap"])
def test_invalid_source_path_stays_unknown(fault):
    raw = frame()
    if fault == "invalid":
        raw.loc[3, "valid"] = False
    elif fault == "epoch":
        raw.loc[3:, "epoch"] = 1
    elif fault == "missing":
        raw = raw.drop(index=3)
    elif fault == "clock":
        raw.loc[3, "server_upper_ms"] = np.nan
    else:
        raw["source_gap"] = False
        raw.loc[3, "source_gap"] = True
    result = quote_path(QuoteSeries(raw), 0, 1, SPEC, 20_000_000_000)
    assert not result["known"] and result["reason"] == "quote_path_gap_or_invalid"


def test_a_known_early_take_does_not_rescue_a_fixed_horizon_tail():
    raw = frame()
    raw.loc[2:, ["bid", "ask"]] = [102.0, 102.01]
    result = quote_path(QuoteSeries(raw), 0, 1, SPEC, 8_000_000_000)
    assert result == {"known": False, "reason": "fixed_horizon_tail"}


def test_exact_sub_dollar_target_is_not_missed_due_to_float_division():
    raw = frame()
    raw[["bid", "ask"]] = [0.06999, 0.07]
    raw.loc[3, ["bid", "ask"]] = [0.07028, 0.07029]
    raw["bid_text"], raw["ask_text"] = raw.bid.map(str), raw.ask.map(str)
    assert 0.07028 / 0.07 - 1 < 0.004
    result = quote_path(QuoteSeries(raw), 0, 1, SPEC, 20_000_000_000)
    independent = scalar_path(raw.to_dict("records"), 0, 1, SPEC, 20_000_000_000)
    assert result["reason"] == independent["reason"] == "take"
    assert result["trigger_monotonic_ns"] == independent["trigger"] == 3_000_000_000
    assert result["exit_monotonic_ns"] == 4_000_000_000


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("fault", [None, "gap", "epoch"])
def test_independent_scalar_replay_agrees_on_known_and_unknown_paths(sign, fault):
    raw = frame()
    raw["bid_text"], raw["ask_text"] = raw.bid.map(str), raw.ask.map(str)
    if fault == "gap":
        raw["source_gap"] = False
        raw.loc[3, "source_gap"] = True
    elif fault == "epoch":
        raw.loc[3:, "epoch"] = 1
    expected = scalar_path(raw.to_dict("records"), 0, sign, SPEC, 20_000_000_000)
    actual = quote_path(QuoteSeries(raw), 0, sign, SPEC, 20_000_000_000)
    assert (actual["known"], actual["reason"]) == (expected["known"], expected["reason"])
    if actual["known"]:
        assert actual["entry_index"] == expected["entry_index"]
        assert actual["exit_index"] == expected["exit_index"]


def test_actual_funding_is_only_charged_during_exposure_using_a_known_mid():
    quotes = QuoteSeries(frame())
    path = quote_path(quotes, 0, 1, SPEC, 20_000_000_000)
    coverage = {"complete": True, "start_ms": 1_000_000, "end_ms": 1_020_000}
    events = [{"time_ms": t, "rate": 0.001} for t in [1_000_000, 1_004_000, 1_007_000, 1_008_000]]
    funding = funding_cash(quotes, path, events, coverage)
    assert funding["known"]
    assert len(funding["events"]) == 1
    assert funding["cash_per_base_unit"] == pytest.approx(-0.001 * 100.005)


def test_missing_funding_or_ambiguous_settlement_time_never_becomes_zero():
    raw = frame()
    raw.loc[1, ["server_lower_ms", "server_upper_ms"]] = [1_000_999, 1_001_001]
    quotes = QuoteSeries(raw)
    path = quote_path(quotes, 0, 1, SPEC, 20_000_000_000)
    coverage = {"complete": True, "start_ms": 1_000_000, "end_ms": 1_020_000}
    result = funding_cash(quotes, path, [{"time_ms": 1_001_000, "rate": 0.001}], coverage)
    assert not result["known"] and result["reason"] == "ambiguous_funding_exposure_boundary"
    coverage["complete"] = False
    assert not funding_cash(quotes, path, [], coverage)["known"]


def test_later_clock_sample_cannot_change_past_quote_timing():
    records = pd.DataFrame(
        {"mono_ns": [5_000_000_000], "wall_ns": [1_005_000_000_000], "event_ms": [1_004_900]}
    )
    samples = [
        {
            "sent_monotonic_ns": 0,
            "received_monotonic_ns": 100_000_000,
            "sent_wall_ns": 1_000_000_000_000,
            "received_wall_ns": 1_000_100_000_000,
            "server_time_ms": 1_000_050,
        }
    ]
    rules = {
        "maximum_clock_probe_age_seconds": 120,
        "minimum_clock_probe_rtt_ms": 0,
        "maximum_clock_probe_rtt_ms": 500,
        "maximum_conservative_event_age_ms": 1000,
    }
    original = clocks_for_receipts(records, samples, rules)
    samples.append(
        dict(
            samples[0],
            received_monotonic_ns=6_000_000_000,
            sent_monotonic_ns=5_000_000_000,
            server_time_ms=9_000_000,
        )
    )
    pd.testing.assert_frame_equal(original, clocks_for_receipts(records, samples, rules))
    assert original.clock_valid.all()
