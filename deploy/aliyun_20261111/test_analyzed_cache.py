"""Exact-value live cache compaction, including pinned NFI callback history audit."""
# ruff: noqa: S101

import ast
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.strategy import IStrategy


class NativeSignals(IStrategy):
    timeframe = "5m"
    can_short = True

    def populate_indicators(self, dataframe, metadata):
        return dataframe


def provider(**overrides):
    return DataProvider({"runmode": RunMode.LIVE, "timeframe": "5m", **overrides}, None)


def test_compaction_releases_full_blocks_and_preserves_every_tail_value():
    frame = pd.DataFrame({
        "date": pd.date_range(end=datetime.now(UTC), periods=800, freq="5min"),
        "close": np.linspace(1.0, 3.0, 800), "signal": np.arange(800, dtype="int64"),
        "tag": ["original tag"] * 800,
    })
    original = frame.copy(deep=True)
    default, compact = provider(), provider()
    compact.set_analyzed_cache_limit(2)
    for dp in (default, compact):
        dp._set_cached_df("BTC/USDT", "5m", frame, CandleType.SPOT)
    full, _ = default.get_analyzed_dataframe("BTC/USDT", "5m")
    saved, _ = compact.get_analyzed_dataframe("BTC/USDT", "5m")
    assert full is frame
    pd.testing.assert_frame_equal(frame, original)
    pd.testing.assert_frame_equal(saved, full.tail(2))
    assert not np.shares_memory(saved.close.to_numpy(), frame.close.to_numpy())
    assert saved.memory_usage(deep=True).sum() < full.memory_usage(deep=True).sum() / 100
    # Subsequent cache replacement must keep current values and the original index.
    replacement = frame.assign(close=frame.close + 1)
    compact._set_cached_df("BTC/USDT", "5m", replacement, CandleType.SPOT)
    pd.testing.assert_frame_equal(
        compact.get_analyzed_dataframe("BTC/USDT", "5m")[0], replacement.tail(2),
    )


@pytest.mark.parametrize("options", [
    {"runmode": RunMode.BACKTEST}, {"runmode": RunMode.HYPEROPT},
    {"api_server": {"enabled": True}}, {"disable_dataframe_checks": True},
    {"external_message_consumer": {"producers": [{"name": "external"}]}},
])
def test_incompatible_history_consumers_cannot_enable_compaction(options):
    with pytest.raises(ValueError, match="consumer contract"):
        provider(**options).set_analyzed_cache_limit(2)


@pytest.mark.parametrize("signals", [
    (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1),
    (1, 1, 1, 1), (0, 0, 0, 0),
])
def test_native_long_short_entry_and_exit_signals_are_identical(signals):
    config = {"runmode": RunMode.LIVE, "trading_mode": TradingMode.FUTURES,
              "timeframe": "5m", "exchange": {}}
    strategy = NativeSignals(config)
    frame = pd.DataFrame({
        "date": pd.date_range(end=datetime.now(UTC), periods=800, freq="5min"),
        "close": np.linspace(1, 2, 800), "enter_tag": "original entry",
        "exit_tag": "original exit",
        **dict(zip(("enter_long", "exit_long", "enter_short", "exit_short"), signals,
                   strict=True)),
    })
    full, compact = frame, frame.tail(2).copy()
    assert strategy.get_entry_signal("BTC/USDT:USDT", "5m", full) == (
        strategy.get_entry_signal("BTC/USDT:USDT", "5m", compact)
    )
    for short in (False, True):
        assert strategy.get_exit_signal("BTC/USDT:USDT", "5m", full, is_short=short) == (
            strategy.get_exit_signal("BTC/USDT:USDT", "5m", compact, is_short=short)
        )


def test_every_pinned_nfi_callback_reads_only_the_last_two_analyzed_rows():
    path = Path(__file__).resolve().parents[1] / "strategy/nfi-x8/NostalgiaForInfinityX8.py"
    tree = ast.parse(path.read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    callbacks = 0
    for method in ast.walk(tree):
        if not isinstance(method, ast.FunctionDef):
            continue
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "get_analyzed_dataframe"]
        if not calls:
            continue
        callbacks += 1
        assert len(calls) == 1
        assignment = parents[calls[0]]
        assert isinstance(assignment, ast.Assign)
        assert ast.unparse(assignment.targets[0]) == "(df, _)"
        for name in ast.walk(method):
            if not isinstance(name, ast.Name) or name.id != "df" or isinstance(name.ctx, ast.Store):
                continue
            parent = parents[name]
            if isinstance(parent, ast.Attribute) and parent.attr == "iloc":
                subscript = parents[parent]
                assert isinstance(subscript, ast.Subscript)
                assert ast.unparse(subscript.slice) in ("-1", "-2")
            else:
                assert isinstance(parent, ast.Call) and ast.unparse(parent.func) == "len"
                compare = parents[parent]
                assert isinstance(compare, ast.Compare)
                assert all(isinstance(v, ast.Constant) and v.value in (1, 2)
                           for v in compare.comparators)
    assert callbacks == 16
