# ruff: noqa: S101
"""Causality and native dynamic lifecycle tests, without fabricated return claims."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from freqtrade.enums import RunMode
from freqtrade.exceptions import OperationalException
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade
from research.crypto.nfi80_historical_universe_20261109.HistoricalVolumePairList import (
    HistoricalVolumePairList,
)
from research.crypto.nfi80_historical_universe_20261109.prepare import HERE, OUT, sha


def plugin(tmp_path, future=False):
    pairs = [f"P{i}/USDT:USDT" for i in range(81)]
    path = tmp_path / "schedule.json"
    path.write_text(
        json.dumps(
            {
                "union": pairs,
                "days": {
                    "2026-01-01": {
                        "pairs": pairs[:80],
                        "latest_input_close": "2026-01-01T00:00:00+00:00"
                        if future
                        else "2025-12-31T23:59:59.999000+00:00",
                    },
                    "2026-01-02": {
                        "pairs": pairs[1:],
                        "latest_input_close": "2026-01-01T23:59:59.999000+00:00",
                    },
                },
            }
        )
    )
    clock = SimpleNamespace(backtest_time=None)
    item = HistoricalVolumePairList(
        Mock(),
        SimpleNamespace(_dataprovider=clock),
        {"runmode": RunMode.BACKTEST},
        {"schedule": str(path), "sha256": sha(path)},
        0,
    )
    return item, clock, pairs


def test_initial_union_and_exact_midnight(tmp_path):
    item, clock, pairs = plugin(tmp_path)
    assert item.gen_pairlist({}) == pairs
    clock.backtest_time = datetime(2026, 1, 1, 23, 55, tzinfo=UTC)
    assert item.gen_pairlist({}) == pairs[:80]
    clock.backtest_time = datetime(2026, 1, 2, tzinfo=UTC)
    assert item.gen_pairlist({}) == pairs[1:]
    clock.backtest_time = datetime(2026, 1, 3, tzinfo=UTC)
    with pytest.raises(OperationalException):
        item.gen_pairlist({})


def test_future_data_rejected(tmp_path):
    with pytest.raises(OperationalException, match="noncausal"):
        plugin(tmp_path, True)


def test_isolated_native_clock_and_removed_entry():
    # Load the exact patched native class without replacing the installed package.
    path = OUT / "engine/freqtrade/optimize/backtesting.py"
    spec = importlib.util.spec_from_file_location("tested_native_historical", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    account = object.__new__(module.Backtesting)
    now = datetime(2026, 1, 2, tzinfo=UTC)
    account.timeframe_td = __import__("datetime").timedelta(minutes=5)
    account.dynamic_pairlist = True
    account.available_pairs = ["removed", "kept"]
    account.required_startup = 0
    account._set_progress_step = Mock()
    account.check_abort = Mock()
    account._increment_progress = Mock()
    account.strategy = SimpleNamespace(bot_loop_start=Mock(), config={"stake_currency": "USDT"})
    account.dataprovider = Mock()
    account.pairlists = SimpleNamespace(whitelist=["kept"])

    def refresh(**kwargs):
        account.dataprovider._set_dataframe_max_date.assert_called_with(now)

    account.pairlists.refresh_pairlist = refresh
    account._capture_wallet = Mock()
    account._sync_pair_index = Mock(return_value=0)
    row = [now, 100, 100, 100, 100]
    account.validate_row = Mock(return_value=row)
    account.check_for_trade_entry = Mock(return_value="long")
    account._time_generator = Mock(return_value=iter([now]))
    account._time_pair_generator_det = Mock(
        return_value=iter([(now, True, False, 0, "removed"), (now, True, False, 0, "kept")])
    )
    prior = LocalTrade.bt_trades_open.copy()
    prior_pp = LocalTrade.bt_trades_open_pp.copy()
    try:
        LocalTrade.bt_trades_open = []
        LocalTrade.bt_trades_open_pp.clear()
        rows = list(
            account.time_pair_generator(now - account.timeframe_td, now, ["removed", "kept"], {})
        )
        assert [r[1] for r in rows] == ["removed", "kept"]
        assert [r[-1] for r in rows] == [None, "long"]
        # The unchanged native held-first generator retains symbols removed from the current pool.
        LocalTrade.bt_trades_open = [SimpleNamespace(pair="removed")]
        account._time_generator_det = Mock(return_value=[(now, True, False, 0)])
        assert [r[-1] for r in Backtesting._time_pair_generator_det(account, now, ["kept"])] == [
            "removed",
            "kept",
        ]
    finally:
        LocalTrade.bt_trades_open = prior
        LocalTrade.bt_trades_open_pp.clear()
        LocalTrade.bt_trades_open_pp.update(prior_pp)


def test_extension_is_bounded_to_clock_and_membership():
    manifest = json.loads((HERE / "engine-manifest.json").read_text())
    assert set(manifest["modified_upstream_files"]) == {
        "freqtrade/data/dataprovider.py",
        "freqtrade/optimize/backtesting.py",
    }
    assert not manifest["official_unmodified"]
    upstream = json.loads(Path(manifest["base_manifest"]["path"]).read_text())
    changed = {n for n, h in upstream["files"].items() if manifest["files"][n] != h}
    assert changed == set(manifest["modified_upstream_files"])


def test_lossless_native_row_blocks(tmp_path):
    import pickle

    from research.crypto.nfi80_historical_universe_20261109.row_storage import RowBlocks

    rows = [
        [datetime(2026, 1, 1, tzinfo=UTC), 0.0000000234 + i, float(i), 1, 0, None, "141 142 "]
        for i in range(97)
    ]
    store = RowBlocks(tmp_path / "rows", block_size=7, maximum_cached=2)
    store.add("held", rows)
    store.add("empty", [])
    assert store["held"][:] == rows
    for recovered, original in zip(store["held"][:], rows, strict=True):
        assert pickle.dumps(recovered, protocol=5) == pickle.dumps(original, protocol=5)
    for index in [96, 0, 7, 31, 6, 40, -1, -97]:
        assert store["held"][index] == rows[index]
        assert len(store.cached) <= 2
    assert len(store["empty"]) == 0
    with pytest.raises(IndexError):
        store["held"][97]
    assert sum(r["rows"] for r in store.receipts.values()) == 97
