"""Frozen bounded storage probe; no account engine start and no old pickle loads."""

import copy
import hashlib
import importlib.util
import json
import os
import pickle
import resource
import shutil
import socket
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from .frame_storage import AnalyzedFrameMapping, UnsupportedFrameOperation, install


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def guard():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss = rss if sys.platform == "darwin" else rss * 1024
    free = shutil.disk_usage(ROOT).free
    if rss > 2 * 1024**3 or free < 1024**3:
        raise RuntimeError(f"Bounded probe resource guard: {rss=}, {free=}")
    return {"peak_rss_bytes": rss, "free_disk_bytes": free}


def exact(actual, expected):
    pd.testing.assert_frame_equal(
        actual,
        expected,
        check_exact=True,
        check_dtype=True,
        check_index_type=True,
        check_column_type=True,
        check_categorical=True,
        check_freq=True,
        check_flags=True,
    )
    assert actual.attrs == expected.attrs
    assert actual.flags.allows_duplicate_labels == expected.flags.allows_duplicate_labels
    for index in range(expected.shape[1]):
        a, b = actual.iloc[:, index], expected.iloc[:, index]
        if isinstance(b.dtype, np.dtype) and b.dtype.kind in "biufcmM":
            assert a.to_numpy().tobytes() == b.to_numpy().tobytes(), b.name
        if isinstance(b.dtype, pd.DatetimeTZDtype):
            assert a.array.asi8.tobytes() == b.array.asi8.tobytes(), b.name


def regenerate(protocol, output):
    """Use official loading/resolution; never construct Backtesting or Wallets."""
    prior = json.loads(Path(protocol["prior_plan"]["path"]).read_text())
    helper = (
        load("nfi_frame_source_verifier", Path(prior["runner_source"]["path"]))
        if "runner_source" in prior
        else load(
            "nfi_frame_source_verifier",
            HERE.with_name("nfi40_allocation_native_20261117_v1") / "native_runner.py",
        )
    )
    candidate = prior["candidates"][0]
    engine = helper.verify(prior, candidate)
    sys.path.insert(0, str(engine))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.configuration import TimeRange
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data import history
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver, StrategyResolver

    assert Path(freqtrade.__file__).resolve() == engine / "freqtrade/__init__.py"
    assert not any(name.startswith("FREQTRADE__") for name in os.environ)
    support = output / "support"
    for relative, receipt in prior["research_support"].items():
        destination = support / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(helper.checked(receipt), destination)
    freqtrade.__path__.append(str(support))
    strategy_path = output / "strategies"
    strategy_path.mkdir()
    for name, receipt in candidate["source_files"].items():
        shutil.copyfile(helper.checked(receipt), strategy_path / name)
    raw = json.loads(helper.checked(candidate["config"]).read_text())
    start = pd.Timestamp(prior["start"])
    end = start + pd.Timedelta(minutes=5 * (4000 - 800 - 1))
    raw.update(
        datadir=prior["datadir"],
        user_data_dir=str(output),
        strategy_path=str(strategy_path),
        timerange=f"{int(start.timestamp())}-{int(end.timestamp())}",
        db_url="sqlite://",
        export="none",
        exportdirectory=str(output / "never_native_results"),
    )
    write(output / "config.json", raw)
    config = setup_optimize_configuration(
        {"config": [str(output / "config.json")], "datadir": prior["datadir"]}, RunMode.BACKTEST
    )
    assert not any(config["exchange"].get(key) for key in ("key", "secret", "password"))
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(helper.checked(prior["market_snapshot"]).read_text())
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange.validate_config(config)
    strategy = StrategyResolver.load_strategy(config)
    provider = DataProvider(config, exchange)
    strategy.dp = provider
    strategy.ft_bot_start()
    pair = prior["pairs"][0]
    data = history.load_data(
        datadir=config["datadir"],
        pairs=[pair],
        timeframe="5m",
        timerange=TimeRange.parse_timerange(config["timerange"]),
        startup_candles=800,
        data_format=config["dataformat_ohlcv"],
        candle_type=config["candle_type_def"],
    )[pair]
    assert len(data) == 4000
    from freqtrade.data.btanalysis.historic_precision import get_tick_size_over_time
    get_tick_size_over_time(data)  # Reproduce native loader-added count columns only.
    expected = strategy.advise_all_indicators({pair: data.copy(deep=True)})[pair]
    assert expected.shape == (4000, 231)
    reference = json.loads(Path(protocol["prior_preflight"]["path"]).read_text())
    receipt = {
        "shape": list(expected.shape),
        "source_frame_pickle_sha256": hashlib.sha256(
            pickle.dumps(expected, protocol=5)
        ).hexdigest(),
        "old_reference_pickle_sha256": reference["uncompressed_reference_pickle_sha256"],
        "old_pickle_loaded": False,
        "native_strategy_resolver": True,
        "native_indicator_regeneration": True,
        "backtesting_constructed": False,
        "wallets_constructed": False,
    }
    receipt["serialized_reference_matches_prior_preflight"] = (
        receipt["source_frame_pickle_sha256"] == receipt["old_reference_pickle_sha256"]
    )
    return expected, config, exchange, strategy, receipt


def callback_parity(strategy, config, frame, direct, blocked, output):
    """Actual NFI callbacks on synthetic detached objects; no native account run."""
    from freqtrade.enums import TradingMode
    from freqtrade.persistence import LocalTrade, Order, Trade
    from freqtrade.persistence.custom_data import CustomDataWrapper
    from freqtrade.resolvers import StrategyResolver

    pair = config["exchange"]["pair_whitelist"][0]
    now = frame.date.iloc[3000].to_pydatetime()
    outcomes = []
    original_trade_db, original_custom_db = Trade.use_db, CustomDataWrapper.use_db
    original_custom_data = CustomDataWrapper.custom_data
    Trade.use_db = False
    CustomDataWrapper.use_db = False
    CustomDataWrapper.custom_data = []
    try:
        for label, provider in (("direct", direct), ("blocks", blocked)):
            cfg = copy.deepcopy(config)
            directory = output / f"callbacks_{label}"
            directory.mkdir()
            cfg["user_data_dir"] = directory
            child = StrategyResolver.load_strategy(cfg)
            child.dp = provider
            child.ft_bot_start()
            try:
                provider._set_dataframe_max_index(pair, 3000)
                close = float(frame.close.iloc[2999])
                confirmations = []
                for side, rate in (
                    ("long", close * 1.001),
                    ("long", close * 1.2),
                    ("short", close * 0.999),
                    ("short", close * 0.8),
                ):
                    tag = (
                        child.long_normal_mode_tags[0]
                        if side == "long"
                        else child.short_normal_mode_tags[0]
                    )
                    confirmations.append(
                        child.confirm_trade_entry(pair, "limit", 1.0, rate, "GTC", now, tag, side)
                    )
                trade = LocalTrade(
                    id=987654,
                    pair=pair,
                    open_date=now,
                    open_rate=100.0,
                    amount=1.0,
                    stake_amount=100.0,
                    is_short=False,
                    leverage=1.0,
                    trading_mode=TradingMode.FUTURES,
                    fee_open=0.0015,
                    fee_close=0.0015,
                    enter_tag="192 ",
                    funding_fees=0.0,
                    max_rate=100.0,
                    min_rate=69.0,
                )
                trade.orders = [
                    Order(
                        order_id="detached-synthetic",
                        ft_order_side="buy",
                        ft_pair=pair,
                        ft_is_open=False,
                        status="closed",
                        order_type="limit",
                        side="buy",
                        price=100.0,
                        average=100.0,
                        amount=1.0,
                        filled=1.0,
                        remaining=0.0,
                        cost=100.0,
                        order_date=now,
                        order_filled_date=now,
                    )
                ]
                child._set_profit_target(pair, "synthetic_readonly_parity", 110.0, 0.1, now)
                reason = child.custom_exit(pair, trade, now, 69.0, -0.31)
                outcomes.append(
                    {
                        "provider": label,
                        "confirmations": confirmations,
                        "synthetic_custom_exit": reason,
                        "profit_target_state": copy.deepcopy(child.target_profit_cache.data),
                    }
                )
            finally:
                child.ft_bot_cleanup()
        assert {k: v for k, v in outcomes[0].items() if k != "provider"} == {
            k: v for k, v in outcomes[1].items() if k != "provider"
        }
        return {
            "passed": True,
            "fixtures": outcomes,
            "scope": "Synthetic detached NFI callback parity, not strategy profit or matching proof",
        }
    finally:
        Trade.use_db = original_trade_db
        CustomDataWrapper.use_db = original_custom_db
        CustomDataWrapper.custom_data = original_custom_data


def main():
    protocol = json.loads((HERE / "protocol_v3.json").read_text())
    freeze = json.loads((HERE / "implementation_freeze_v3.json").read_text())
    assert all(sha(path) == value for path, value in freeze["files"].items())
    output = Path(protocol["output_root"])
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    original_connect = socket.socket.connect

    def no_network(*args, **kwargs):
        raise RuntimeError("Storage probe attempted network")

    socket.socket.connect = no_network
    exchange = strategy = None
    try:
        guard()
        source, config, exchange, strategy, source_receipt = regenerate(protocol, output)
        from freqtrade.data.dataprovider import DataProvider
        from freqtrade.enums import CandleType

        pair = config["exchange"]["pair_whitelist"][0]
        candle_type = config["candle_type_def"]
        direct, blocked = DataProvider(config, None), DataProvider(config, None)
        stores = install(blocked, output / "native_blocks")
        store = stores[-1]
        direct._set_cached_df(pair, "5m", source, candle_type)
        blocked._set_cached_df(pair, "5m", source, candle_type)
        key = (pair, "5m", candle_type)
        lazy, refresh = store[key]
        assert store[key][1] is refresh
        assert lazy.shape == source.shape
        exact(blocked.get_analyzed_dataframe(pair, "5m")[0], pd.DataFrame())
        exact(blocked.get_analyzed_dataframe("MISSING", "5m")[0], pd.DataFrame())
        rng = np.random.default_rng(protocol["random_seed"])
        counts = {}
        for label, data in [
            ("native_source", source),
            ("native_analyzed", strategy.ft_advise_signals(source.copy(deep=True), {"pair": pair})),
            ("enriched", pd.concat([source, source, source.iloc[:1003]], ignore_index=True)),
        ]:
            if label == "enriched":
                data.index = pd.RangeIndex(37, 37 + 3 * len(data), 3, name="fixed_arbitrary_index")
                data["category_fixture"] = pd.Categorical(
                    np.resize(["a", "b", None], len(data)),
                    categories=["b", "a", "unused"],
                    ordered=True,
                )
                data["nullable_fixture"] = pd.array(
                    np.resize([1, None, -2], len(data)), dtype="Int64"
                )
                data["object_fixture"] = pd.Series(
                    np.resize(["text", None, ""], len(data)), index=data.index, dtype=object
                )
                data["float_bits_fixture"] = np.resize(
                    np.array([0.0, -0.0, np.nan, np.inf, -np.inf]), len(data)
                )
                data.attrs = {"synthetic": True, "nested": {"roles": ["source", "storage"]}}
                data.flags.allows_duplicate_labels = False
            direct._set_cached_df(pair, "5m", data, candle_type)
            blocked._set_cached_df(pair, "5m", data, candle_type)
            ends = rng.integers(1000, len(data) + 1, size=100).tolist()
            special = [0, 1, 999, 1000, len(data), len(data) + 100]
            special += [x for x in [4095, 4096, 4097, 8191, 8192, 8193] if x <= len(data)]
            for end in ends + special:
                direct._set_dataframe_max_index(pair, end)
                blocked._set_dataframe_max_index(pair, end)
                expected, _ = direct.get_analyzed_dataframe(pair, "5m")
                actual, lastrefresh = blocked.get_analyzed_dataframe(pair, "5m")
                exact(actual, expected)
                assert lastrefresh is store[key][1]
            lazy, _ = store[key]
            for selection in [
                slice(None),
                slice(-1300, -2),
                slice(4000, 4200),
                slice(8190, 8200),
                slice(8, 3),
            ]:
                exact(lazy.iloc[selection], data.iloc[selection])
            counts[label] = {
                "shape": list(data.shape),
                "random_native_1000_row_slices": 100,
                "boundary_slices": len(special),
                "direct_lazy_slices": 5,
            }
            if label == "enriched":
                enriched = data
                enriched_ends = ends
        # Additional fixed index classes and categories/UTC/object/NaN schema roundtrips.
        for label, index in [
            (
                "datetime",
                pd.date_range(
                    "2025-01-01", periods=len(enriched), freq="5min", tz="UTC", name="utc_index"
                ),
            ),
            (
                "multi",
                pd.MultiIndex.from_arrays(
                    [np.arange(len(enriched)) % 3, np.arange(len(enriched))], names=["group", "id"]
                ),
            ),
        ]:
            small = enriched[
                [
                    "date",
                    "category_fixture",
                    "nullable_fixture",
                    "object_fixture",
                    "float_bits_fixture",
                ]
            ].copy()
            small.index = index
            small.flags.allows_duplicate_labels = True
            blocked._set_cached_df(label, "5m", small, candle_type)
            for end in [4097, 8193, len(small)]:
                blocked._set_dataframe_max_index(label, end)
                exact(blocked.get_analyzed_dataframe(label, "5m")[0], small.iloc[end - 1000 : end])
        empty = enriched.iloc[:0]
        blocked._set_cached_df("EMPTY", "5m", empty, candle_type)
        blocked._set_dataframe_max_index("EMPTY", 1)
        exact(blocked.get_analyzed_dataframe("EMPTY", "5m")[0], empty)
        failures = []
        for label, operation in [
            ("scalar_iloc", lambda: lazy.iloc[-1]),
            ("stepped_iloc", lambda: lazy.iloc[::2]),
            ("copy_api", lambda: lazy.copy()),
            ("column_api", lambda: lazy["close"]),
        ]:
            try:
                operation()
            except UnsupportedFrameOperation:
                failures.append(label)
            else:
                raise AssertionError(f"Unsupported operation escaped: {label}")
        # Returned object cells are detached from immutable cached blocks.
        nested = pd.DataFrame(
            {"x": [{"list": [1]}], "date": pd.date_range("2025-01-01", periods=1, tz="UTC")}
        )
        blocked._set_cached_df("OBJECT", "5m", nested, candle_type)
        blocked._set_dataframe_max_index("OBJECT", 1)
        changed, _ = blocked.get_analyzed_dataframe("OBJECT", "5m")
        changed.iloc[0, 0]["list"].append(2)
        exact(blocked.get_analyzed_dataframe("OBJECT", "5m")[0], nested)
        store.flush()
        mutation = AnalyzedFrameMapping(output / "mutation", runmode="backtest")
        mutation[("X", "5m", CandleType.FUTURES)] = (pd.DataFrame({"x": [1, 2]}), datetime.now(UTC))
        mutation[("X", "5m", CandleType.FUTURES)][0].iloc[:1]
        next(iter(mutation._cache.values()))[0].iloc[0, 0] = 999
        try:
            mutation.flush()
        except ValueError:
            failures.append("cached_block_mutation_flush")
        else:
            raise AssertionError("Flush accepted mutated block")
        corrupt = AnalyzedFrameMapping(output / "corruption", runmode="backtest")
        corrupt[("X", "5m", CandleType.FUTURES)] = (pd.DataFrame({"x": [1, 2]}), datetime.now(UTC))
        corrupt_path = Path(corrupt.receipts[0]["path"])
        raw = corrupt_path.read_bytes()
        corrupt_path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        try:
            corrupt[("X", "5m", CandleType.FUTURES)][0].iloc[:1]
        except ValueError:
            failures.append("corruption_rejected_before_unpickle")
        else:
            raise AssertionError("Corrupted bytes loaded")
        lru = AnalyzedFrameMapping(output / "lru", runmode="backtest")
        for number in range(34):
            k = (f"K{number}", "5m", candle_type)
            lru[k] = (pd.DataFrame({"x": [number]}), datetime.now(UTC))
            exact(lru[k][0].iloc[:1], pd.DataFrame({"x": [number]}))
        assert len(lru._cache) == 32 and lru.stats["evictions"] == 2
        exact(lru[("K0", "5m", candle_type)][0].iloc[:1], pd.DataFrame({"x": [0]}))
        # Benchmark the unchanged legacy Zstd mapping, but only bytes generated here.
        legacy = load("frozen_legacy_zstd", protocol["legacy_storage"]["path"])
        full = legacy.DiskMapping(output / "full_cache", 4)
        full[key] = (enriched, refresh)
        full_path = full.paths[key]
        full_compressed_sha = sha(full_path)
        with pa.CompressedInputStream(str(full_path), "zstd") as stream:
            full_raw = stream.read()
        assert full_raw == pickle.dumps((enriched, refresh), protocol=5)
        blocked._set_cached_df(pair, "5m", enriched, candle_type)
        benchmark = []
        for cold in [True, False]:
            full.cached.clear()
            store.drop_cached_blocks()
            full_read = 0
            block_before = dict(store.stats)
            t = time.perf_counter()
            for end in enriched_ends:
                if cold:
                    full.cached.clear()
                if key not in full.cached:
                    assert sha(full_path) == full_compressed_sha
                    full_read += full_path.stat().st_size
                value, _ = full[key]
                result = value.iloc[max(0, end - 1000) : end]
                assert len(result) == 1000
            full_seconds = time.perf_counter() - t
            t = time.perf_counter()
            for end in enriched_ends:
                if cold:
                    store.drop_cached_blocks()
                blocked._set_dataframe_max_index(pair, end)
                result, _ = blocked.get_analyzed_dataframe(pair, "5m")
                assert len(result) == 1000
            block_seconds = time.perf_counter() - t
            benchmark.append(
                {
                    "mode": "cold_each_read" if cold else "resident_lru",
                    "queries": 100,
                    "legacy_full_seconds": full_seconds,
                    "block_seconds": block_seconds,
                    "legacy_compressed_read_bytes": full_read,
                    "block_compressed_read_bytes": store.stats["compressed_read_bytes"]
                    - block_before["compressed_read_bytes"],
                    "legacy_decompressed_read_bytes": len(full_raw) * (100 if cold else 1),
                    "block_decompressed_read_bytes": store.stats["decompressed_read_bytes"]
                    - block_before["decompressed_read_bytes"],
                }
            )
        direct._set_cached_df(pair, "5m", source, candle_type)
        blocked._set_cached_df(pair, "5m", source, candle_type)
        try:
            callbacks = callback_parity(strategy, config, source, direct, blocked, output)
        except Exception as error:
            callbacks = {
                "passed": False,
                "gap": repr(error),
                "traceback": traceback.format_exc(),
                "full_callback_parity_proven": False,
            }
        store.flush()
        native_file = Path(protocol["native_dataprovider"]["path"])
        assert sha(native_file) == protocol["native_dataprovider"]["sha256"]
        blocked.clear_cache()
        exact(blocked.get_analyzed_dataframe(pair, "5m")[0], pd.DataFrame())
        for receipt in store.receipts:
            path = Path(receipt["path"])
            assert sha(path) == receipt["compressed_sha256"]
            raw = pa.Codec("zstd").decompress(path.read_bytes(), receipt["raw_bytes"], asbytes=True)
            assert hashlib.sha256(raw).hexdigest() == receipt["raw_sha256"]
        result = {
            "passed": True,
            "verified_at": datetime.now(UTC).isoformat(),
            "protocol_sha256": sha(HERE / "protocol_v3.json"),
            "source": source_receipt,
            "slice_cases": counts,
            "unsupported_and_corruption_rejections": failures,
            "additional_index_classes": [
                "DatetimeIndex UTC",
                "MultiIndex",
                "nondefault RangeIndex",
            ],
            "empty_no_index_missing_key_clear_native_semantics": True,
            "lastrefresh_original_object_preserved": True,
            "chunk_serialized_byte_roundtrip": True,
            "logical_slices_exact_including_numeric_bits": True,
            "whole_concat_dataframe_pickle_byte_identity_claimed": False,
            "lru_stats": lru.stats,
            "block_receipts": store.receipts,
            "benchmarks": benchmark,
            "callbacks": callbacks,
            "native_get_analyzed_dataframe_unmodified": True,
            "resources": guard(),
            "elapsed_seconds": time.perf_counter() - started,
            "old_pickle_loaded": False,
            "backtesting_start_called": False,
            "native_accounts_completed": 0,
            "full_account_parity_proven": False,
            "full40_performance_proven": False,
            "live_controller_modified": False,
            "orders_enabled": False,
            "financial_performance_proven": False,
            "goal_complete": False,
        }
        write(HERE / "probe_result_v3.json", result)
        print(
            json.dumps(
                {
                    "passed": True,
                    "source_shape": source_receipt["shape"],
                    "slices": counts,
                    "benchmarks": benchmark,
                    "callbacks_passed": callbacks["passed"],
                    "resources": result["resources"],
                }
            ),
            flush=True,
        )
    finally:
        socket.socket.connect = original_connect
        if strategy is not None:
            strategy.ft_bot_cleanup()
        if exchange is not None:
            exchange.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write(
            HERE / "probe_failure_v3.json",
            {
                "at": datetime.now(UTC).isoformat(),
                "error": repr(error),
                "traceback": traceback.format_exc(),
                "financial_start_called": False,
            },
        )
        raise
