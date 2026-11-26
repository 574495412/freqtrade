"""Register and independently audit four matched native Freqtrade accounts."""

import argparse
from datetime import UTC, datetime
from fcntl import LOCK_EX, flock
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import LOCK, ROOT, checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1.audit import account

from .inputs import HERE, OUT, PARENT


V003 = HERE.parent / "nfi80_historical_universe_20261109/v003"
CAP = HERE.parent / "nfi80_online_rebuy_cap_20261125_v1"
RUNNER = "research.crypto.nfi80_historical_universe_20261109.v003.native_runner"
COSTS = (15, 8)
CAP_ROLE = "short_rebuy_cap_12"


def frozen_sources():
    plan = read(PARENT / "plan.json")
    return {
        "original": V003 / "original-plan.json",
        "fixed_boost_12": checked(plan["predecessor_plan"]),
        "boost_14": PARENT / "plan.json",
        "short_rebuy_cap_12": CAP / "plan.json",
    }


def write_once(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError(f"Frozen account evidence changed at {path}")
    else:
        save(path, value)
    return receipt(path)


def verify_coverage():
    protocol = receipt(HERE / "protocol.json")
    start, end = map(pd.Timestamp, read(checked(protocol))["recent_window"])
    coverage_path = OUT / "coverage.json"
    coverage = read(coverage_path)
    assert coverage["protocol"] == protocol
    assert (pd.Timestamp(coverage["start"]), pd.Timestamp(coverage["end_exclusive"])) == (start, end)
    schedule = read(checked(coverage["schedule"]))
    union = schedule["union"]
    market = read(checked(coverage["market_rules"]))
    assert sorted(market["markets"]) == sorted(market["leverage_tiers"]) == union
    assert sorted(coverage["raw"]) == union
    assert len(schedule["days"]) == len(pd.date_range(start, end, freq="D", inclusive="left"))
    for evidence in coverage["raw"].values():
        for source in evidence["raw"]:
            checked(source)
    for source in schedule["daily_sources"].values():
        checked(source["source"])
        checked(source["prefix"])
        for item in source["raw"]:
            checked(item)
    data = OUT / "candles"
    stored = read(checked(coverage["files"]))
    assert len(stored) == 8 * len(union)
    for relative, digest in stored.items():
        checked({"path": data / relative, "sha256": digest})
    write_once(HERE / "recent_input_preflight.json", {
        "protocol": protocol, "coverage": receipt(coverage_path), "schedule": coverage["schedule"],
        "start": start.isoformat(), "end_exclusive": end.isoformat(),
        "daily_clocks": len(schedule["days"]), "contract_union": len(union),
        "normalized_inputs_verified": len(stored), "market_rules": coverage["market_rules"],
        "orders_enabled": False,
    })
    print("verified matched account inputs", len(schedule["days"]), len(union), len(stored), flush=True)
    return start, end, coverage, union


def register(scope="complete"):
    start, end, coverage, union = verify_coverage()
    protocol = coverage["protocol"]
    exit_mark = end - pd.Timedelta(minutes=5)
    reports = OUT / "registrations"
    reports.mkdir(exist_ok=True)
    data = OUT / "candles"
    marks = {p: receipt(data / "futures" / (p.replace("/", "_").replace(":", "_") + "-1h-mark.feather"))
             for p in union}
    common = {
        "start": start.isoformat(), "end": end.isoformat(), "comparison_end": exit_mark.isoformat(),
        "periods": [{"name": "continuous", "start": start.isoformat(), "end": exit_mark.isoformat()}],
        "pairs": union, "datadir": str(data), "historical_schedule": coverage["schedule"],
        "market_snapshot": coverage["market_rules"], "data_receipts": coverage["files"],
        "marks": marks, "protocol_receipt": protocol, "total_cost_bps_per_side": list(COSTS),
        "recent_coverage": receipt(OUT / "coverage.json"),
        "comparison_purpose": "Frozen 16-day matched recent-window account validation",
        "automatic_parameter_optimization": False, "orders_enabled": False, "production_eligible": False,
    }
    registrations = {}
    fixed_config = None
    fixed_implementation = None
    expected_slots = {"original": 12, "fixed_boost_12": 12, "boost_14": 14, "short_rebuy_cap_12": 12}
    for role, source_path in frozen_sources().items():
        if (scope == "controls" and role == CAP_ROLE) or (scope == "cap_diagnostic" and role != CAP_ROLE):
            continue
        discovery_dir = CAP if role == CAP_ROLE else PARENT
        discovery = []
        for cost in (15,) if scope == "cap_diagnostic" else COSTS:
            path = discovery_dir / f"comparison_{cost}bp.json"
            outcome = read(path)
            assert outcome["candidate"]["five_minute_nav_reconciliation"]["passed"]
            if cost == 15:
                assert outcome["advance"]
            discovery.append(receipt(path))
        source = read(source_path)
        candidate, = source["candidates"]
        assert candidate["max_open_trades"] == expected_slots[role]
        config = read(checked(candidate["config"]))
        config["exchange"]["pair_whitelist"] = union
        config["pairlists"] = [{
            "method": "HistoricalVolumePairList",
            "schedule": coverage["schedule"]["path"],
            "sha256": coverage["schedule"]["sha256"],
        }]
        config["bot_name"] = "nfi80-recent-matched-accounts"
        invariants = invariant_config(config)
        implementation = {key: source[key] for key in (
            "engine_commit", "engine_manifest", "environment", "row_storage", "runner_sha256",
            "initial_equity_usdt", "allowed_leverage", "observation_frequency", "cost_model",
        )}
        if fixed_config is None:
            fixed_config, fixed_implementation = invariants, implementation
        else:
            assert invariants == fixed_config and implementation == fixed_implementation
        ref = write_once(reports / f"{role}-config.json", config)
        case = {**candidate, "name": role, "config": ref}
        plan = {**source, **common, "candidates": [case], "discovery_parent": receipt(source_path),
                "discovery_reports": discovery}
        suffix = ""
        if scope == "cap_diagnostic":
            control = read(reports / "fixed_boost_12-plan.json")
            assert invariants == invariant_config(read(checked(control["candidates"][0]["config"])))
            assert all(control[key] == plan[key] for key in (
                "pairs", "start", "end", "comparison_end", "periods", "datadir", "market_snapshot",
                "data_receipts", "marks", "engine_manifest", "environment", "initial_equity_usdt",
                "allowed_leverage", "observation_frequency", "cost_model",
            ))
            failures = [CAP / f"execution_8bp_failed_{label}low_disk.json" for label in ("", "bounded_")]
            assert all(read(path)["returncode"] == 73 for path in failures)
            plan["comparison_purpose"] = "Recent-window diagnostic while 8bp discovery remains unadmitted"
            plan["unadmitted_discovery_attempts"] = [receipt(path) for path in failures]
            suffix = "-diagnostic"
        registrations[role] = write_once(reports / f"{role}{suffix}-plan.json", plan)
    suffix = "" if scope == "complete" else f"_{scope}"
    registry = write_once(reports / f"registry{suffix}.json", registrations)
    print("frozen registrations", registry["path"], flush=True)
    return registrations


def remove_completed_cache(folder, summary):
    assert summary["five_minute_nav_reconciliation"]["passed"]
    assert read(folder / "native_row_storage_audit.json")["exact_serialized_roundtrip"]
    cache = folder / "lossless_cache"
    if not cache.exists():
        return
    assert cache.resolve(strict=True) == cache
    paths = list(cache.rglob("*"))
    assert not any(p.is_symlink() for p in paths)
    files = [p for p in paths if p.is_file()]
    pattern = r"(?:analyzed_[12]|indicators)/[0-9a-f]{64}\.pkl|native_rows/[0-9a-f]{64}-[0-9]+\.pkl\.zstd"
    assert all(re.fullmatch(pattern, str(p.relative_to(cache))) for p in files)
    removed = sum(p.stat().st_size for p in files)
    shutil.rmtree(cache)
    save(folder / "regenerable_cache.json", {"bytes_removed": removed, "files_removed": len(files)})


def execute(registrations, scope="complete"):
    results = OUT / ("accounts_diagnostic_cap" if scope == "cap_diagnostic" else "accounts")
    results.mkdir(exist_ok=True)
    for cost in COSTS:
        cases, configs = {}, []
        for role, plan_path in registrations.items():
            plan = read(checked(plan_path))
            folder = results / f"{role}_{cost}bp"
            completion = results / f"{role}_{cost}bp.json"
            if not completion.exists():
                log_path = results / f"{role}_{cost}bp.log"
                with LOCK.open("a") as lock:
                    flock(lock, LOCK_EX)
                    if folder.exists() or log_path.exists():
                        raise FileExistsError("Preserve the previous native attempt")
                    if shutil.disk_usage(OUT).free < 4 * 1024 ** 3:
                        raise OSError("Native replay disk guard")
                    print("starting", role, cost, flush=True)
                    with log_path.open("x") as log:
                        run = subprocess.run([
                            sys.executable, "-B", "-m", RUNNER, "--plan", plan_path["path"],
                            "--candidate", role, "--cost-bps", str(cost), "--output", str(folder),
                        ], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    save(completion, {"returncode": run.returncode, "folder": str(folder),
                                      "finished_at_utc": datetime.now(UTC).isoformat(), "orders_enabled": False})
                    run.check_returncode()
            assert read(completion)["returncode"] == 0
            summary, _, config = account(plan, folder, cost, check_sizing=role != "original")
            assert summary["five_minute_nav_reconciliation"]["passed"]
            if role == "original":
                assert not (folder / "stake-decisions.jsonl").exists()
            cases[role] = summary
            configs.append(config)
            save(folder / "independent_account_reconciliation.json", summary)
            remove_completed_cache(folder, summary)
            print("completed", role, cost, summary["periods"]["continuous"], flush=True)
        assert all(config == configs[0] for config in configs)
        suffix = "" if scope == "complete" else f"_{scope}"
        save(HERE / f"recent_window_{cost}bp{suffix}.json", {
            "accounts": cases, "cost_bps_per_side": cost, "all_roles_complete": scope == "complete",
            "scope": scope,
            "orders_enabled": False, "production_eligible": False, "window_is_restart": True,
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--execute-controls", action="store_true")
    mode.add_argument("--execute-cap-diagnostic", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        verify_coverage()
    else:
        scope = ("controls" if args.execute_controls else
                 "cap_diagnostic" if args.execute_cap_diagnostic else "complete")
        registrations = register(scope)
        if args.execute or args.execute_controls or args.execute_cap_diagnostic:
            execute(registrations, scope)
