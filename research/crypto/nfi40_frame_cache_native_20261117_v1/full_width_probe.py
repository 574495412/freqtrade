"""Single real BTC full-width frame, six synthetic cache keys; no accounts."""

import gc
import hashlib
import io
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

import pandas as pd
import pyarrow as pa
import psutil

from .frame_storage_v3 import install
from .probe_v5 import exact, load, write

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def guard(label):
    limits = json.loads((HERE / "full_width_protocol.json").read_text())["resource_limits"]
    rss = psutil.Process().memory_info().rss
    available = psutil.virtual_memory().available
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = peak if sys.platform == "darwin" else peak * 1024
    with (HERE / "full_width_phases.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "phase": label,
                    "rss_bytes": rss,
                    "peak_rss_bytes": peak,
                    "available_bytes": available,
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            + "\n"
        )
    if rss > limits["max_rss_bytes"] or available < limits["min_available_bytes"]:
        raise MemoryError(f"Resource guard at {label}: {rss=}, {available=}")
    return {"phase": label, "rss_bytes": rss, "peak_rss_bytes": peak, "available_bytes": available}


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
    end = pd.Timestamp(prior["end"]) - pd.Timedelta(seconds=1)
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
    loaded_rows = len(data)
    first_date, last_date = str(data.date.iloc[0]), str(data.date.iloc[-1])
    from freqtrade.data.btanalysis.historic_precision import get_tick_size_over_time

    get_tick_size_over_time(data)  # Reproduce native loader-added count columns only.
    expected = strategy.advise_all_indicators({pair: data.copy(deep=True)})[pair]
    assert expected.shape == (loaded_rows, 231)
    receipt = {
        "indicator_shape": list(expected.shape),
        "loaded_rows": loaded_rows,
        "first_date": first_date,
        "last_date": last_date,
        "timerange": config["timerange"],
        "configured_pairs": len(config["exchange"]["pair_whitelist"]),
        "actual_loaded_base_pairs": [pair],
        "startup_candles": 800,
        "old_pickle_loaded": False,
        "backtesting_constructed": False,
        "wallets_constructed": False,
    }
    return expected, config, exchange, strategy, receipt


class CountedRaw(io.RawIOBase):
    def __init__(self, underlying, counter, field):
        self.underlying = underlying
        self.counter = counter
        self.field = field

    def readable(self):
        return True

    def readinto(self, buffer):
        result = self.underlying.read(len(buffer))
        buffer[: len(result)] = result
        self.counter[self.field] += len(result)
        return len(result)


def legacy_mapping(module, root, maximum):
    """Original writer/eviction semantics; reader only adds provenance and IO counters."""

    class Measured(module.DiskMapping):
        def __init__(self):
            super().__init__(root, maximum)
            self.generated_hashes = {}
            self.counters = dict(
                loads=0,
                hits=0,
                guard_read_bytes=0,
                compressed_read_bytes=0,
                decompressed_read_bytes=0,
            )

        def _write(self, key, value):
            super()._write(key, value)
            self.generated_hashes[key] = sha(self.paths[key])

        def __getitem__(self, key):
            if key in self.cached:
                value = self.cached.pop(key)
                self.counters["hits"] += 1
            else:
                path = self.paths[key]
                if path.is_symlink() or sha(path) != self.generated_hashes[key]:
                    raise ValueError("Own generated legacy cache changed before unpickle")
                self.counters["guard_read_bytes"] += path.stat().st_size
                self.counters["loads"] += 1
                with path.open("rb") as file:
                    counted_file = io.BufferedReader(
                        CountedRaw(file, self.counters, "compressed_read_bytes")
                    )
                    with pa.CompressedInputStream(counted_file, "zstd") as raw:
                        with io.BufferedReader(
                            CountedRaw(raw, self.counters, "decompressed_read_bytes")
                        ) as stream:
                            value = pickle.load(stream)  # noqa: S301 - this object wrote and checked it
            self.cached[key] = value
            self._trim()
            return value

    return Measured()


def benchmark(label, frame, config, exchange, protocol, output):
    from freqtrade.data.dataprovider import DataProvider

    provider = DataProvider(config, exchange)
    resources = [guard(label + ":before")]
    if label == "blocks32":
        mapping = install(provider, output / label)[0]
    else:
        module = load("fullwidth_legacy_" + label, Path(protocol["legacy_storage"]["path"]))
        mapping = legacy_mapping(module, output / label, int(label[-1]))
        provider._DataProvider__cached_pairs = mapping
    pairs = [f"CACHE_ALIAS_{i}/USDT:USDT" for i in range(6)]
    # All six native cache assignments receive the SAME existing BTC frame object.
    # This intentionally measures storage working-set pressure, not six assets.
    begun = time.perf_counter()
    for pair in pairs:
        provider._set_cached_df(pair, "5m", frame, config["candle_type_def"])
        resources.append(guard(label + ":write:" + pair))
    write_seconds = time.perf_counter() - begun
    if label != "blocks32":
        # Native analyzed caches receive signals here; prime all six keys once
        # to turn legacy alias references into independent deserialized entries.
        # Clear only AFTER the complete writer phase, outside measured reads.
        mapping.cached.clear()
    first_pass = None
    later_passes = []
    verification_seconds = 0.0
    read_seconds = 0.0
    for cycle in range(10):
        cycle_seconds = 0.0
        end = protocol["first_slice_end"] + cycle
        for pair in pairs:
            provider._set_dataframe_max_index(pair, end)
            begun = time.perf_counter()
            actual, _ = provider.get_analyzed_dataframe(pair, "5m")
            duration = time.perf_counter() - begun
            cycle_seconds += duration
            read_seconds += duration
            begun = time.perf_counter()
            exact(actual, frame.iloc[end - 1000 : end])
            verification_seconds += time.perf_counter() - begun
            del actual
            guard(label + ":read")
        if cycle == 0:
            first_pass = cycle_seconds
        else:
            later_passes.append(cycle_seconds)
        resources.append(guard(label + ":cycle:" + str(cycle)))
    counters = dict(mapping.stats) if label == "blocks32" else dict(mapping.counters)
    if label == "blocks32":
        mapping.flush()
    receipt = {
        "mode": label,
        "passed": True,
        "actual_market_assets": 1,
        "cache_aliases": 6,
        "cycles": 10,
        "native_1000_row_slices": 60,
        "shape": list(frame.shape),
        "write_seconds": write_seconds,
        "first_read_cycle_seconds": first_pass,
        "remaining_nine_cycles_seconds": sum(later_passes),
        "individual_warm_cycle_seconds": later_passes,
        "total_native_read_seconds": read_seconds,
        "exact_verification_seconds_excluded_from_read_time": verification_seconds,
        "read_counters": counters,
        "resources": resources,
        "legacy_maximum_observed_cached": getattr(mapping, "maximum_observed_cached", None),
        "legacy_writeback_skips": getattr(mapping, "writeback_skips", None),
        "reader_instrumentation": "Legacy writer/trim unchanged; SHA check plus counted IO layers before own-process pickle loading; block map unchanged",
    }
    write(HERE / f"full_width_{label}_result.json", receipt)
    del mapping, provider
    gc.collect()
    return receipt


def main():
    protocol = json.loads((HERE / "full_width_protocol.json").read_text())
    output = Path(protocol["output_root"])
    output.mkdir(parents=True, exist_ok=False)
    original_connect = socket.socket.connect

    def offline(*args, **kwargs):
        raise RuntimeError("Full-width storage probe attempted network")

    socket.socket.connect = offline
    begun = time.perf_counter()
    try:
        guard("startup")
        frame, config, exchange, strategy, source = regenerate(protocol, output)
        source["indicator_compute_and_setup_seconds"] = time.perf_counter() - begun
        source["resources_after_indicators"] = guard("indicators")
        signal_started = time.perf_counter()
        # Native ft_advise_signals mutates this frame in place before DP caching.
        frame = strategy.ft_advise_signals(frame, {"pair": protocol["actual_pair"]})
        source["signal_seconds"] = time.perf_counter() - signal_started
        source["analyzed_shape"] = list(frame.shape)
        assert frame.shape == (source["loaded_rows"], 237)
        source["resources_after_signals"] = guard("signals")
        source["logical_dataframe_bytes_deep"] = int(
            frame.memory_usage(index=True, deep=True).sum()
        )
        write(HERE / "full_width_source_result.json", source)
        results = []
        for mode in protocol["mode_order"]:
            results.append(benchmark(mode, frame, config, exchange, protocol, output))
        result = dict(
            passed=True,
            source=source,
            modes=results,
            elapsed_seconds=time.perf_counter() - begun,
            final_resources=guard("complete"),
            native_financial_accounts=0,
            existing_processes_modified=False,
            historical_precision_corrected=False,
            full40_performance_proven=False,
            execution_replacement_admitted=False,
        )
        write(HERE / "full_width_result.json", result)
        print(
            json.dumps(
                {
                    "passed": True,
                    "shape": list(frame.shape),
                    "modes": [
                        {"mode": r["mode"], "read_seconds": r["total_native_read_seconds"]}
                        for r in results
                    ],
                }
            ),
            flush=True,
        )
    except BaseException as error:
        write(
            HERE / "full_width_failure.json",
            {
                "passed": False,
                "error": repr(error),
                "traceback": traceback.format_exc(),
                "native_financial_accounts": 0,
                "existing_processes_modified": False,
            },
        )
        raise
    finally:
        socket.socket.connect = original_connect


if __name__ == "__main__":
    main()
