"""Prepare or execute one registered P1 native account. No live trading path exists."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
import traceback

HERE = Path(__file__).resolve().parent
ENV = HERE.parents[1] / "p0/environment"
sys.path.insert(0, str(ENV))
import guard
import standalone_adapter as adapter

ROOT, PROGRAM = guard.ROOT, guard.PROGRAM
MIN_FREE_DISK_BYTES = 10 * 1024**3


def read(path):
    return json.loads(Path(path).read_text())


def save_new(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, default=str)
        stream.write("\n")


def rec(path):
    path = guard.local_path(str(path))
    return {"path": str(path.relative_to(ROOT)), "sha256": guard.sha(path)}


def retain_result_hashes(output):
    """Hash durable evidence without traversing the reconstructable cache."""
    records = []
    for directory, subdirs, names in os.walk(output, followlinks=False):
        directory = Path(directory)
        if any((directory / name).is_symlink() for name in [*subdirs, *names]):
            raise ValueError("Refuse symlink-containing account evidence")
        if directory == output:
            subdirs[:] = [name for name in subdirs if name != "lossless_cache"]
        for name in sorted(names):
            path = directory / name
            if directory == output and name in ("progress.json", "retained_artifact_hashes.json", "cache_cleanup.json"):
                continue
            records.append({"relative_path": str(path.relative_to(output)),
                            "bytes": path.stat().st_size, "sha256": guard.sha(path)})
    receipt = output / "retained_artifact_hashes.json"
    save_new(receipt, {"scope": "durable current-run evidence before lossless_cache cleanup",
                      "excluded": ["lossless_cache", "progress.json", "retained_artifact_hashes.json", "cache_cleanup.json"],
                      "files": sorted(records, key=lambda item: item["relative_path"])})
    return rec(receipt)


def script_receipts():
    paths = [HERE / "run_native.py", HERE / "observer.py", ENV / "guard.py", ENV / "standalone_adapter.py",
             ENV / "source_environment.json", ENV / "data_manifest.json", ENV / "prepared_plans.json",
             HERE / "sources/average/AverageStrategy.py", HERE / "sources/average/LICENSE"]
    return [rec(path) for path in paths]


def lookup(run_id):
    registry = read(PROGRAM / "experiment_registry.json")
    registered = next((run for run in registry["runs"] if run["id"] == run_id), None)
    if not registered or registered["baseline_id"] not in ("B2_AVERAGE_NATIVE", "B3_NFI_X8_NATIVE"):
        raise ValueError("Only frozen B2/B3 P1 registry cells are accepted")
    return registered


def research_output(value):
    path = guard.local_path(value)
    if not path.is_relative_to(ROOT / "user_data/crypto_quant"):
        raise ValueError("Output must be under isolated user_data/crypto_quant")
    return path


def build_plan(run, config_path):
    program = read(PROGRAM / "plan.json")
    dataset_id = run["market"] + "_" + run["window_id"]
    plan = adapter.load_prepared_plan(dataset_id)
    native_candidate = plan["candidates"][0]
    native_raw = read(guard.checked(native_candidate["config"]))
    if run["baseline_id"] == "B2_AVERAGE_NATIVE":
        keep = ("stake_currency", "stake_amount", "dry_run_wallet", "max_open_trades", "tradable_balance_ratio",
                "fiat_display_currency", "unfilledtimeout", "entry_pricing", "exit_pricing", "trading_mode",
                "margin_mode", "dataformat_ohlcv", "dataformat_trades", "pairlists", "internals", "exchange")
        raw = {key: deepcopy(native_raw[key]) for key in keep if key in native_raw}
        raw.update(strategy="AverageStrategy", timeframe="4h", timeframe_detail="5m", startup_candle_count=0,
                   minimal_roi={"0": 0.5}, stoploss=-0.2, trailing_stop=False, use_custom_stoploss=False,
                   position_adjustment_enable=False, stake_amount="unlimited", max_open_trades=6,
                   tradable_balance_ratio=0.99,
                   order_types={"entry": "limit", "exit": "limit", "stoploss": "limit",
                                "stoploss_on_exchange": False, "stoploss_on_exchange_interval": 60})
        source_path = HERE / "sources/average/AverageStrategy.py"
        expected = "63606866fdf1cb3d71dc39a951ed0784a31686690b1c4014c12d2481c8dbf094"
        if guard.sha(source_path) != expected:
            raise ValueError("Pinned upstream AverageStrategy changed")
        if list(source_path.parent.glob("*.json")):
            raise ValueError("External strategy parameter file forbidden")
        candidate = {"name": "average-native", "strategy": "AverageStrategy",
                     "config": {"path": str(config_path), "sha256": None},
                     "source_files": {"AverageStrategy.py": {"path": str(source_path), "sha256": expected}}}
    else:
        raw = deepcopy(native_raw)
        candidate = deepcopy(native_candidate)
        candidate["config"] = {"path": str(config_path), "sha256": None}
    raw.update(dry_run=True, dry_run_wallet=1000, api_server={"enabled": False}, telegram={"enabled": False},
               export="trades", backtest_cache="none", recursive_strategy_search=False)
    raw.pop("webhook", None)
    for key in ("key", "secret", "password", "uid"):
        if raw["exchange"].get(key):
            raise ValueError("Credential-bearing historical config rejected")
        raw["exchange"].pop(key, None)
    if raw["exchange"]["pair_whitelist"] != program["markets"][run["market"]]["pairs"]:
        raise ValueError("Frozen universe/order mismatch")
    cost = next(c for c in program["markets"][run["market"]]["cost_scenarios"] if c["id"] == run["cost_id"])
    raw["fee"] = cost["total_bps_per_side"] / 10000
    plan.update(schema="freqtrade.rd.p1-native-account.v1", registered_run_id=run["id"],
                registered_baseline=run["baseline_id"], candidates=[candidate],
                total_cost_bps_per_side=[cost["total_bps_per_side"]],
                native_observer_source=plan["observer_source"],
                observer_source={"path": str(HERE / "observer.py"), "sha256": guard.sha(HERE / "observer.py")},
                memory_guard_bytes=program["budgets"]["max_rss_bytes"],
                wall_guard_seconds=program["budgets"]["wall_seconds_per_run"],
                scope="native account engineering reproduction plus explicitly classified 5m reference; economic qualification false")
    return plan, raw, cost


def manifest_strategy_fields(plan, config):
    # The frozen Binance X8 source declares 800; its exchange-specific overrides
    # apply only to other exchanges. Do not add a new config override to B3.
    startup = config.get("startup_candle_count", 800)
    if "startup_candle_count" not in config and (
        plan["registered_baseline"] != "B3_NFI_X8_NATIVE" or config["exchange"]["name"] != "binance"
    ):
        raise ValueError("Unresolved strategy startup default")
    return {"resolved_strategy_config_hash": plan["candidates"][0]["config"]["sha256"],
            "strategy_main_timeframe": config["timeframe"],
            "detail_timeframe": config.get("timeframe_detail"),
            "startup_candle_count": startup,
            "order_types": config["order_types"], "stake_amount": config["stake_amount"],
            "strategy_config_hash_scope": "registered config; full runtime effective_config retained in output"}


def prepare(run_id, registration_dir, output):
    run = lookup(run_id)
    registration, output = research_output(registration_dir), research_output(output)
    if registration.exists() or output.exists() or registration == output or registration.is_relative_to(output) or output.is_relative_to(registration):
        raise ValueError("Registration and account output must be separate fresh directories")
    guard.verify_environment(read(ENV / "source_environment.json"))
    dataset_id = run["market"] + "_" + run["window_id"]
    guard.verify_dataset(adapter.dataset_record(dataset_id), payload=True)
    registration.mkdir(parents=True)
    plan, config, cost = build_plan(run, registration / "config.json")
    save_new(registration / "config.json", config)
    plan["candidates"][0]["config"]["sha256"] = guard.sha(registration / "config.json")
    save_new(registration / "plan.json", plan)
    manifest = {"schema": "freqtrade.rd.p1-native-run-manifest.v1", "registered_at_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id, "registry_record": run, "freeze_sha256": guard.sha(PROGRAM / "freeze.json"),
                "source_hashes": script_receipts(), "plan": rec(registration / "plan.json"),
                "dependency_versions": read(ENV / "source_environment.json")["dependencies"],
                "data_receipt_hashes": [plan["data_receipts"]],
                "universe_hash": read(PROGRAM / "plan.json")["markets"][run["market"]]["universe_sha256"],
                "risk_profile": "native_baseline", "RD1_applied": False,
                "risk_contract_hash": hashlib.sha256(json.dumps({"risk_profile": "native_baseline", "config_sha256": plan["candidates"][0]["config"]["sha256"]}, sort_keys=True).encode()).hexdigest(),
                "cost_scenario": cost, "window": {"start": plan["start"], "end_exclusive": plan["end"]},
                "valuation_endpoint": adapter.dataset_record(dataset_id)["new_valuation_endpoint"],
                "command": [".venv-paper/bin/python", "-B", str(HERE.relative_to(ROOT) / "run_native.py"), "--execute", str((registration / "run_manifest.json").relative_to(ROOT))],
                "isolated_output_path": str(output.relative_to(ROOT)), "start_utc": None,
                "candidate_readiness": {"source_environment": "passed", "data_hashes": "passed", "date_grid": "passed",
                                        "causality": "unresolved_X8_alerts" if run["baseline_id"] == "B3_NFI_X8_NATIVE" else "not_yet_diagnosed",
                                        "five_minute_mark": "not_certified", "scope": "engineering_only"},
                "resource_guards": {"min_free_disk_bytes": MIN_FREE_DISK_BYTES,
                                    "cache_cleanup": "only fresh account output/lossless_cache after verified completion"},
                "orders_enabled": False, "economic_qualified": False, "runtime_modified": False}
    manifest.update(manifest_strategy_fields(plan, config))
    save_new(registration / "run_manifest.json", manifest)
    print(json.dumps({"prepared": run_id, "manifest": str((registration / "run_manifest.json").relative_to(ROOT)), "financial_runs": 0}))


def verify_registered_plan(plan, candidate, manifest):
    guard.verify_environment(read(ENV / "source_environment.json"))
    if guard.sha(PROGRAM / "freeze.json") != manifest["freeze_sha256"]:
        raise ValueError("Program freeze identity differs")
    for item in manifest["source_hashes"]:
        guard.checked(item)
    plan_path = guard.checked(manifest["plan"])
    if plan != read(plan_path) or candidate != plan["candidates"][0]:
        raise ValueError("Runtime plan differs from registered plan")
    if manifest["registry_record"] != lookup(manifest["run_id"]):
        raise ValueError("Run registry differs")
    guard.verify_dataset(adapter.dataset_record(plan["dataset_id"]), payload=True)
    for name in ("storage_source", "observer_source", "native_observer_source", "market_snapshot", "environment", "runner"):
        guard.checked(plan[name])
    for item in (candidate["config"], *candidate["source_files"].values(), *plan["marks"].values()):
        guard.checked(item)
    expected_fields = manifest_strategy_fields(plan, read(guard.checked(candidate["config"])))
    if any(manifest.get(name) != value for name, value in expected_fields.items()):
        raise ValueError("Manifest strategy fields differ from registered config/defaults")
    if plan["research_support"] or plan["RD1_applied"] or plan["risk_profile"] != "native_baseline":
        raise ValueError("Native baseline acquired an unregistered risk modification")
    return ROOT


def execute(manifest_path):
    manifest_path = research_output(manifest_path)
    manifest = read(manifest_path)
    if manifest["orders_enabled"] or manifest["economic_qualified"] or manifest["RD1_applied"]:
        raise ValueError("Invalid execution authorization flags")
    plan_path = guard.checked(manifest["plan"])
    plan = read(plan_path)
    candidate = plan["candidates"][0]
    output = research_output(manifest["isolated_output_path"])
    if output.exists():
        raise ValueError("Output already exists; retries require a new registered directory")
    verify_registered_plan(plan, candidate, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ROOT / "user_data/crypto_quant/rd_program_20261105_native_worker.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(output.parent).free < MIN_FREE_DISK_BYTES:
            save_new(manifest_path.parent / "failure.json", {"run_id": manifest["run_id"],
                     "type": "DiskResourceGuard", "minimum_free_bytes": MIN_FREE_DISK_BYTES,
                     "before_financial_start": True, "orders_enabled": False})
            raise RuntimeError("Less than 10 GiB free disk; no financial run started")
        guard.install_runtime_guards()
        path = guard.checked(plan["runner"])
        spec = importlib.util.spec_from_file_location("rd_pinned_native_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.verify = lambda current_plan, current_candidate: verify_registered_plan(current_plan, current_candidate, manifest)
        started = datetime.now(timezone.utc).isoformat()
        save_new(manifest_path.parent / "start.json", {"start_utc": started, "manifest_sha256": guard.sha(manifest_path), "orders_enabled": False})
        disk_monitor_done = threading.Event()

        def disk_monitor():
            while not disk_monitor_done.wait(2):
                free = shutil.disk_usage(output.parent).free
                if free < MIN_FREE_DISK_BYTES:
                    failure = {"run_id": manifest["run_id"], "type": "DiskResourceGuard",
                               "free_bytes": free, "minimum_free_bytes": MIN_FREE_DISK_BYTES,
                               "orders_enabled": False, "economic_qualified": False}
                    destination = output if output.exists() else manifest_path.parent
                    (destination / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
                    os._exit(74)

        threading.Thread(target=disk_monitor, daemon=True).start()
        try:
            result = module.run(plan_path, candidate["name"], manifest["cost_scenario"]["total_bps_per_side"], output)
            observation = read(output / "five_minute_observer_receipt.json")
            import pandas as pd
            frame = pd.read_parquet(output / "five_minute_reference_equity.parquet")
            endpoint = pd.Timestamp(manifest["valuation_endpoint"])
            final = frame.loc[frame.date == endpoint]
            equity = float(final.equity.iloc[0]) if len(final) == 1 else None
            cash_audit = read(output / "fill_cash_audit.json")
            save_new(output / "p1_result.json", {"run_id": manifest["run_id"], "start_utc": started,
                     "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                     "source_environment_checked": True, "native_fill_cash_audit_passed": cash_audit["passed"],
                     "old_hourly_anchor_metrics": result["periods"],
                     "new_endpoint": manifest["valuation_endpoint"], "new_endpoint_reference_equity": equity,
                     "new_endpoint_reference_return": None if equity is None else equity / 1000 - 1,
                     "observer": observation, "native_result": rec(output / "result.json"),
                     "economic_qualified": False, "orders_enabled": False, "RD1_applied": False,
                     "qualification_blocks": ["causality acceptance not completed", "5m executable/mark NAV not certified", "historical selection bias retained"]})
            artifact_receipt = retain_result_hashes(output)
            # Cache is generated by this new run, is not an input or audit result,
            # and can be reconstructed from the pinned source/data. Keep all
            # registration, native exports, reference NAV and cash evidence.
            cache = output / "lossless_cache"
            if cache.exists():
                if cache.is_symlink() or read(output / "registered_plan.json") != plan:
                    raise ValueError("Refuse cleanup of unowned or mismatched cache")
                cache_files = list(cache.rglob("*"))
                if any(path.is_symlink() for path in cache_files):
                    raise ValueError("Refuse symlink-containing cache cleanup")
                deleted_files = [path for path in cache_files if path.is_file()]
                byte_count = sum(path.stat().st_size for path in deleted_files)
                shutil.rmtree(cache)
                save_new(output / "cache_cleanup.json", {"removed_relative_directory": "lossless_cache",
                         "generated_files": len(deleted_files), "bytes_reclaimed": byte_count,
                         "reason": "reconstructable current-run cache after source/data/cash verification",
                         "retained_artifact_hash_receipt": artifact_receipt,
                         "retained": ["registered_plan.json", "config.json", "effective_config.json", "native_results",
                                      "hourly_equity.parquet", "five_minute_reference_equity.parquet", "fill_cash_audit.json", "result.json", "p1_result.json"]})
        except BaseException as exc:
            failure = {"run_id": manifest["run_id"], "type": type(exc).__name__, "message": str(exc),
                       "traceback": traceback.format_exc(), "orders_enabled": False, "economic_qualified": False}
            if output.exists():
                (output / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
            else:
                save_new(manifest_path.parent / "failure.json", failure)
            raise
        finally:
            disk_monitor_done.set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", metavar="REGISTERED_RUN_ID")
    group.add_argument("--execute", metavar="RUN_MANIFEST_JSON")
    parser.add_argument("--registration-dir")
    parser.add_argument("--output")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.prepare:
        if not args.registration_dir or not args.output:
            parser.error("--prepare requires --registration-dir and --output")
        prepare(args.prepare, args.registration_dir, args.output)
    else:
        execute(args.execute)


if __name__ == "__main__":
    main()
