"""Reproduce one author-published NFI account with the author's own engine.

The original strategy and execution engine remain byte-identical. Large
dataframe mappings spill losslessly to disk to fit the local machine.
"""

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import zipfile
from importlib.metadata import version
from pathlib import Path

from research.crypto.nfi_original_storage import DiskMapping, disk_map_each


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def run(plan_path, output):  # noqa: C901 - one bounded reproduction worker
    output.mkdir(parents=True, exist_ok=False)
    plan = json.loads(plan_path.read_text())
    save(output / "registered_plan.json", plan)
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Unexpected configuration environment overrides")
    for name, digest in plan["source_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen source changed: " + name)
    sys.path.insert(0, plan["engine_source"])
    import psutil

    import freqtrade
    from freqtrade.commands import Arguments
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize import backtesting as native

    if freqtrade.__version__ != "2026.9":
        raise ValueError("Author engine was not selected")
    save(
        output / "runtime.json",
        {
            "python": sys.version,
            "platform": sys.platform,
            "engine_path": freqtrade.__file__,
            "engine_version": freqtrade.__version__,
            "dependencies": {name: version(name) for name in plan["dependency_versions"]},
        },
    )
    for name, required in plan["dependency_versions"].items():
        if version(name) != required:
            raise ValueError("Author dependency differs: " + name)
    userdir = output / "user_data"
    for name in ["strategies", "logs", "backtest_results", "data"]:
        (userdir / name).mkdir(parents=True, exist_ok=True)
    (output / "native_results").mkdir()
    argv = [
        "backtesting",
        "--strategy",
        "NostalgiaForInfinityX8",
        "--strategy-path",
        plan["strategy_source"],
        "--timerange",
        plan["timerange"],
        "--user-data-dir",
        str(userdir),
        "--datadir",
        plan["datadir"],
        "--export-directory",
        str(output / "native_results"),
        "--export",
        "signals",
        "--cache",
        "none",
        "--breakdown",
        "day",
    ]
    for config in plan["original_configs"]:
        argv.extend(["--config", config])
    config = setup_optimize_configuration(Arguments(argv).get_parsed_arg(), RunMode.BACKTEST)
    config["fee"] = plan.get("fee_per_side", 0.0005)
    config.update(plan.get("economic_overrides", {}))
    config["exchange"]["enable_ws"] = False
    for key in ["ccxt_config", "ccxt_async_config"]:
        ccxt_config = config["exchange"].setdefault(key, {})
        ccxt_config["timeout"] = 20000
        ccxt_config.setdefault("options", {})["fetchMarkets"] = {"types": ["linear"]}
        ccxt_config["options"]["fetchCurrencies"] = False
    # Preserve the existing process proxy route without changing system settings.
    config["exchange"]["ccxt_config"]["http_proxy"] = "http://127.0.0.1:7897"
    config["exchange"]["ccxt_async_config"]["http_proxy"] = "http://127.0.0.1:7897"
    if config["runmode"] != RunMode.BACKTEST or any(
        config["exchange"].get(key) for key in ["key", "secret", "password"]
    ):
        raise ValueError("Only an anonymous native backtest is allowed")
    save(output / "effective_config.json", config)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    print({"stage": "initializing_original_engine", "pid": os.getpid()}, flush=True)
    started = time.monotonic()
    finished = threading.Event()

    def monitor():
        process = psutil.Process()
        maximum_rss = 0
        while not finished.wait(5):
            rss = process.memory_info().rss
            maximum_rss = max(maximum_rss, rss)
            state = {
                "pid": os.getpid(),
                "elapsed_seconds": time.monotonic() - started,
                "rss_bytes": rss,
                "max_observed_rss_bytes": maximum_rss,
            }
            save(output / "process_progress.json", state)
            if (
                rss > plan["memory_guard_bytes"]
                or state["elapsed_seconds"] > plan["wall_guard_seconds"]
            ):
                save(output / "resource_guard.json", state)
                os._exit(73)

    threading.Thread(target=monitor, daemon=True).start()
    cache = output / "lossless_cache"
    cache.mkdir()
    original_trim = native.trim_dataframes
    serial = 0

    def spill_trim(frames, timerange, startup_candles):
        nonlocal serial
        serial += 1
        return disk_map_each(
            original_trim, frames, cache / f"trim_{serial}", timerange, startup_candles
        )

    native.trim_dataframes = spill_trim
    account = native.Backtesting(config)
    provider = account.dataprovider
    provider._DataProvider__cached_pairs_backtesting = DiskMapping(cache / "raw_provider", 8)
    original_clear = provider.clear_cache

    def clear_with_disk_cache():
        nonlocal serial
        original_clear()
        serial += 1
        provider._DataProvider__cached_pairs = DiskMapping(cache / f"analyzed_{serial}", 8)

    provider.clear_cache = clear_with_disk_cache
    clear_with_disk_cache()
    for strategy in account.strategylist:
        original_advise = strategy.advise_all_indicators

        def spill_advise(frames, operation=original_advise):
            result = DiskMapping(cache / "indicators", 3)
            for count, (pair, frame) in enumerate(frames.items(), 1):
                result.update(operation({pair: frame}))
                if count <= 3 or count % 10 == 0:
                    print(
                        {"stage": "original_indicators", "pairs": count, "last_pair": pair},
                        flush=True,
                    )
            return result

        strategy.advise_all_indicators = spill_advise
    observer = None
    if plan.get("observer_module"):
        spec = importlib.util.spec_from_file_location("cz_nfi_observer", plan["observer_module"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observer = module.attach(account, plan, output)
    try:
        account.start()
        if observer:
            archive_path = next((output / "native_results").glob("*.zip"))
            with zipfile.ZipFile(archive_path) as archive:
                name = next(
                    n
                    for n in archive.namelist()
                    if n.endswith(".json") and not n.endswith("_config.json")
                )
                stats = json.loads(archive.read(name))["strategy"]["NostalgiaForInfinityX8"]
            observer.finish(plan["observation_end"], stats["final_balance"])
        save(
            output / "completion.json",
            {
                "native_run_completed": True,
                "elapsed_seconds": time.monotonic() - started,
                "author_match_not_yet_audited": True,
                "orders_enabled": False,
            },
        )
    finally:
        finished.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve())
