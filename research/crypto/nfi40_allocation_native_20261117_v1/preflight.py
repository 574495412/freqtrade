"""Bounded actual source/official-constructor storage probe, never a backtest."""

import hashlib
import json
import os
import pickle
import shutil
import socket
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from research.crypto.nfi40_allocation_native_20261117_v1.native_runner import (
    checked,
    load_module,
    save,
    sha,
    verify,
)


HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    frozen = json.loads((HERE / "implementation_freeze.json").read_text())
    assert all(sha(p) == digest for p, digest in frozen["files"].items())
    assert not any(k.startswith("FREQTRADE__") for k in os.environ)
    candidate = plan["candidates"][0]
    engine = verify(plan, candidate)
    sys.path.insert(0, str(engine))
    import pandas as pd
    import psutil
    import pyarrow as pa

    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize import backtesting as native
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    assert Path(freqtrade.__file__).resolve() == engine / "freqtrade/__init__.py"
    environment = json.loads(checked(plan["environment"]).read_text())
    assert all(version(name) == wanted for name, wanted in environment["dependencies"].items())
    output = Path(plan["output_root"]) / "preflight"
    output.mkdir(exist_ok=False)
    support = output / "support"
    for relative, record in plan["research_support"].items():
        target = support / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked(record), target)
    freqtrade.__path__.append(str(support))
    strategies = output / "strategies"
    strategies.mkdir()
    for name, record in candidate["source_files"].items():
        shutil.copyfile(checked(record), strategies / name)
    start = pd.Timestamp(plan["start"])
    end = start + pd.Timedelta(minutes=5 * (4000 - 800 - 1))
    raw = json.loads(checked(candidate["config"]).read_text())
    raw.update(
        datadir=plan["datadir"],
        user_data_dir=str(output),
        strategy_path=str(strategies),
        timerange=f"{int(start.timestamp())}-{int(end.timestamp())}",
        exportdirectory=str(output / "native_results"),
        db_url="sqlite://",
    )
    save(output / "config.json", raw)
    config = setup_optimize_configuration(
        {
            "config": [str(output / "config.json")],
            "datadir": plan["datadir"],
        },
        RunMode.BACKTEST,
    )
    assert config["exchange"]["pair_whitelist"] == plan["pairs"] and len(plan["pairs"]) == 40
    assert config["dry_run"] is True and config["dry_run_wallet"] == 500
    assert config["max_open_trades"] == 6 and config["trading_mode"] == "futures"
    assert config["margin_mode"] == "isolated" and config["fee"] == 0.0015
    assert not any(config["exchange"].get(k) for k in ["key", "secret", "password"])
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    markets = json.loads(checked(plan["market_snapshot"]).read_text())
    exchange._markets = markets["markets"]
    exchange._leverage_tiers = markets["leverage_tiers"]
    exchange._api.set_markets(markets["markets"], markets["currencies"])
    exchange._api_async.set_markets(markets["markets"], markets["currencies"])
    exchange.validate_config(config)

    def no_network(*args, **kwargs):
        raise RuntimeError("Bounded frozen preflight attempted network")

    socket.socket.connect = no_network
    process = psutil.Process()
    started = time.monotonic()
    finished = threading.Event()
    resources = {
        "maximum_rss_bytes": process.memory_info().rss,
        "minimum_free_disk_bytes": shutil.disk_usage(output).free,
    }

    def observe():
        rss = process.memory_info().rss
        free = shutil.disk_usage(output).free
        resources["maximum_rss_bytes"] = max(resources["maximum_rss_bytes"], rss)
        resources["minimum_free_disk_bytes"] = min(resources["minimum_free_disk_bytes"], free)
        resources["elapsed_seconds"] = time.monotonic() - started
        return rss, free

    def monitor():
        while not finished.wait(0.2):
            rss, free = observe()
            if (
                rss > plan["memory_guard_bytes"]
                or free < plan["minimum_free_disk_bytes"]
                or resources["elapsed_seconds"] > plan["wall_guard_seconds"]
            ):
                save(output / "resource_guard.json", resources)
                os._exit(73)

    rss, free = observe()
    assert rss < plan["memory_guard_bytes"] and free > plan["minimum_free_disk_bytes"]
    threading.Thread(target=monitor, daemon=True).start()
    account = None
    try:
        account = native.Backtesting(config, exchange=exchange)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        assert account.required_startup == strategy.startup_candle_count == 800
        assert account.pairlists.whitelist == plan["pairs"]
        profit_cache = list(output.glob("nfix8-profit_max-*.json"))
        assert not profit_cache, "A previous account profit cache was loaded"
        data, timerange = account.load_bt_data()
        assert list(data) == plan["pairs"]
        pair = plan["pairs"][0]
        frame = data[pair].copy(deep=True)
        assert len(frame) == 4000
        assert frame.date.iloc[0] == start - pd.Timedelta(minutes=5 * 800)
        assert frame.date.iloc[-1] == end
        assert (
            pd.DatetimeIndex(frame.date)
            .as_unit("ns")
            .equals(pd.date_range(frame.date.iloc[0], end, freq="5min").as_unit("ns"))
        )
        expected = strategy.advise_all_indicators({pair: frame.copy(deep=True)})[pair]
        before = pickle.dumps(expected, protocol=5)
        expected_rows = account._get_ohlcv_as_lists({pair: expected.copy(deep=True)})[pair]
        cache = output / "lossless_cache"
        cache.mkdir()
        adapter = load_module("nfi40_preflight_storage", checked(plan["storage_adapter"]))
        installed = adapter.install(account, native, plan, cache, output)
        actual_mapping = strategy.advise_all_indicators({pair: frame.copy(deep=True)})
        actual = actual_mapping[pair]
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        raw_pickle = pickle.dumps(actual, protocol=5)
        compressed = actual_mapping.paths[pair]
        with pa.CompressedInputStream(str(compressed), "zstd") as stream:
            recovered = stream.read()
        assert recovered == raw_pickle
        # Force the same Zstd map through its actual read path, not just its RAM LRU.
        actual_mapping.cached.clear()
        pd.testing.assert_frame_equal(actual_mapping[pair], expected, check_exact=True)
        reference_trim = installed["original_trim"]({pair: expected}, timerange, 800)[pair]
        lazy = native.trim_dataframes(actual_mapping, timerange, 800)
        pd.testing.assert_frame_equal(lazy[pair], reference_trim, check_exact=True)
        uncompressed_bytes = len(raw_pickle)
        compressed_bytes = compressed.stat().st_size
        compressed_sha = sha(compressed)
        row_blocks = account._get_ohlcv_as_lists(actual_mapping)
        recovered_rows = row_blocks[pair][:]
        pd.testing.assert_frame_equal(
            pd.DataFrame(recovered_rows), pd.DataFrame(expected_rows), check_exact=True
        )
        assert not LocalTrade.bt_trades and not LocalTrade.bt_trades_open
        assert not list((output / "native_results").glob("*.zip"))
        assert account.pairlists.whitelist == plan["pairs"]
        assert sha(checked(candidate["source_files"]["NostalgiaForInfinityX8.py"])) == (
            "fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3"
        )
        observe()
        receipt = {
            "passed": True,
            "verified_at": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(HERE / "plan.json"),
            "configured_ordered_pairs": plan["pairs"],
            "source_pair": pair,
            "source_rows": len(frame),
            "source_columns": len(expected.columns),
            "source_frame_bytes_deep": int(expected.memory_usage(index=True, deep=True).sum()),
            "source_input_first": str(frame.date.iloc[0]),
            "source_input_last": str(frame.date.iloc[-1]),
            "original_startup_rows": 800,
            "financial_start_called": False,
            "native_trades_created": 0,
            "native_wallets_completed": 0,
            "source_indicators_direct_vs_zstd_callback_exact": True,
            "uncompressed_reference_pickle_sha256": hashlib.sha256(before).hexdigest(),
            "zstd_serialized_pickle_sha256": hashlib.sha256(raw_pickle).hexdigest(),
            "zstd_recovered_bytes_identical": True,
            "zstd_actual_disk_reload_exact": True,
            "pickle_bytes": uncompressed_bytes,
            "compressed_bytes": compressed_bytes,
            "compressed_sha256_before_consumed_frame_release": compressed_sha,
            "lazy_trim_exact": True,
            "native_row_blocks_exact": True,
            "native_rows": len(expected_rows),
            "resources": resources,
            "cold_account_profit_cache": True,
            "old_pickle_cache_reused": False,
            "full40_resource_feasibility_proven": False,
            "full40_feasibility_cannot_be_inferred_from_this_single_frame": True,
            "conditional_old_execution_model": True,
            "orders_enabled": False,
        }
        verify(plan, candidate)
        save(HERE / "preflight.json", receipt)
        print(json.dumps(receipt, indent=2), flush=True)
    finally:
        finished.set()
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        save(
            HERE / "preflight_failure.json",
            {
                "at": datetime.now(UTC).isoformat(),
                "traceback": traceback.format_exc(),
                "financial_start_called": False,
                "orders_enabled": False,
            },
        )
        raise
