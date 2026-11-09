# ruff: noqa: S101
"""Causality, recoverable risk eligibility and exact native frame storage tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from freqtrade.enums import CandleType, RunMode
from research.crypto.nfi_blacklist_rules_20261109.HistoricalVolumePairList import RuleBook
from research.crypto.nfi_blacklist_rules_20261109.inputs import ref, structural_rules
from research.crypto.nfi_blacklist_rules_20261109.storage import DiskMapping


def fixture_payload(tmp_path, variant):
    dates = pd.date_range("2025-10-01", "2026-01-15", freq="D", tz="UTC")
    close = 100 * np.exp(0.08 * np.sin(np.arange(len(dates)) * np.pi / 2))
    payload = {"variant": variant, "daily_inputs": {}, "structural_blacklist": []}
    for i in range(84):
        frame = pd.DataFrame(
            {
                "date": dates,
                "close_time": dates + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 100.0,
                "quote_volume": 1000.0 - i,
            }
        )
        if i == 0:
            frame.loc[frame.date == pd.Timestamp("2025-12-31", tz="UTC"), "high"] *= 3
        path = tmp_path / f"{i}.parquet"
        frame.to_parquet(path, index=False)
        payload["daily_inputs"][f"P{i:03}/USDT:USDT"] = ref(path)
    return payload


def make(payload):
    exchange = Mock()
    exchange.ohlcv_candle_limit.return_value = 1500
    return RuleBook(
        payload,
        exchange,
        SimpleNamespace(),
        {"runmode": RunMode.BACKTEST, "candle_type_def": CandleType.FUTURES},
    )


def test_native_gate_recovery_and_fixed_rules(tmp_path):
    payload = fixture_payload(tmp_path, "native_risk")
    book = make(payload)
    early = book.select(pd.Timestamp("2026-01-01", tz="UTC"))
    assert early["admitted"] and "P000/USDT:USDT" not in early["pairs"]
    assert early["risk_exclusions"]["P000/USDT:USDT"] == "RangeStabilityFilter"
    late = book.select(pd.Timestamp("2026-01-12", tz="UTC"))
    assert "P000/USDT:USDT" in late["pairs"]
    assert all(len(v) == 11 for v in book.bridge.windows.values())


def test_future_bar_cannot_change_previous_selection(tmp_path):
    payload = fixture_payload(tmp_path, "structural")
    before = make(payload).select(pd.Timestamp("2026-01-01", tz="UTC"))
    pair = "P083/USDT:USDT"
    path = payload["daily_inputs"][pair]["path"]
    f = pd.read_parquet(path)
    f.loc[f.date == pd.Timestamp("2026-01-05", tz="UTC"), "quote_volume"] = 1e12
    f.to_parquet(path, index=False)
    payload["daily_inputs"][pair] = ref(path)
    book = make(payload)
    assert book.select(pd.Timestamp("2026-01-01", tz="UTC"))["pairs"] == before["pairs"]
    assert book.select(pd.Timestamp("2026-01-06", tz="UTC"))["pairs"][0] == pair


def test_no_silent_pool_padding(tmp_path):
    payload = fixture_payload(tmp_path, "structural")
    payload["structural_blacklist"] = ["P00[0-5]/.*"]
    result = make(payload).select(pd.Timestamp("2026-01-01", tz="UTC"))
    assert len(result["pairs"]) == 78 and not result["admitted"]


def test_strong_storage_keeps_types_and_mutations(tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=3000, freq="5min", tz="UTC"),
            "float64": np.arange(3000, dtype=np.float64) / 37,
            "tag": ["141 ", None, "62 "] * 1000,
            "int64": np.arange(3000, dtype=np.int64),
        }
    )
    store = DiskMapping(tmp_path / "cache", 1)
    store["a"] = frame.copy()
    store["b"] = frame.copy()
    pd.testing.assert_frame_equal(store["a"], frame, check_exact=True)
    store["a"].loc[0, "float64"] = 123.456789123456
    store["b"]
    recovered = store["a"]
    assert recovered.loc[0, "float64"] == 123.456789123456
    assert recovered.dtypes.equals(frame.dtypes)
    assert len(structural_rules()) == 7
    with pytest.raises(KeyError):
        store["missing"]
