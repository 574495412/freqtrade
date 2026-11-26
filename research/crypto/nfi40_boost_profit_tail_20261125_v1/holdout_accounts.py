"""Run the frozen holdout for the candidate and both matched controls."""

import argparse
from datetime import timedelta
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
import subprocess
import sys

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked, save,
)

from .audit import analyze, audit_tail
from .holdout_inputs import END, OUT as INPUTS, START
from .run import HERE, LOCK, ROOT, read, receipt


ROLES = ("original", "fixed_boost", "candidate")
SOURCE_NAMES = ("NostalgiaForInfinityX8.py", "StrengthAllocation.py")
COST = 15
MUTABLE_CONFIG_FIELDS = frozenset({
    "strategy", "config_files", "user_data_dir", "strategy_path", "exportdirectory",
    "max_open_trades",
})


def invariant_config(config):
    fixed = {key: value for key, value in config.items() if key not in MUTABLE_CONFIG_FIELDS}
    if "original_config" in fixed:
        fixed["original_config"] = invariant_config(fixed["original_config"])
    return fixed


def register(study, plan_path):
    discovery = study / "comparison_15bp.json"
    if not read(discovery)["advance"]:
        raise ValueError("The main comparison has not met the frozen advance rule")
    parent = read(study / "plan.json")
    coverage_path = INPUTS / "coverage.json"
    coverage = read(coverage_path)
    if (coverage["errors"] or set(coverage["completed"]) != set(parent["pairs"])
            or coverage["start"] != str(START) or coverage["end_exclusive"] != str(END)):
        raise ValueError("Incomplete preregistered holdout data")
    for row in coverage["completed"].values():
        for record in row["raw"]:
            checked(record)
    datadir = INPUTS / "candles"
    marks = {
        pair: receipt(datadir / "futures" / (
            pair.replace("/", "_").replace(":", "_") + "-1h-mark.feather"
        ))
        for pair in parent["pairs"]
    }
    candidate = parent["candidates"][0]
    fixed_parent = read(checked(parent["parent_plan"]))
    fixed = next(row for row in fixed_parent["candidates"] if row["strategy"] == "FlatTopBoost")
    common = read(checked(fixed["config"]))
    source = candidate["source_files"]
    source_sets = (
        {SOURCE_NAMES[0]: source[SOURCE_NAMES[0]]},
        {name: source[name] for name in SOURCE_NAMES},
        source,
    )
    cases = []
    classes = ("NostalgiaForInfinityX8", "FlatTopBoost", candidate["strategy"])
    for role, strategy, sources in zip(ROLES, classes, source_sets, strict=True):
        config_path = study / f"holdout_{role}_config.json"
        config = read(checked(candidate["config"])) if role == "candidate" else common
        save(config_path, {**config, "strategy": strategy})
        cases.append({
            **candidate, "name": role, "strategy": strategy,
            "config": receipt(config_path), "source_files": sources,
            "max_open_trades": config["max_open_trades"],
        })
    plan = {
        **parent, "start": START.isoformat(), "end": END.isoformat(),
        "datadir": str(datadir), "marks": marks, "candidates": cases,
        "periods": [{
            "name": "reserved-november-2026",
            "start": START.isoformat(),
            "end": (END - timedelta(hours=1)).isoformat(),
        }],
        "total_cost_bps_per_side": [COST], "coverage": receipt(coverage_path),
        "data_receipts": receipt(INPUTS / "data-receipts.json"),
        "trigger": receipt(discovery),
    }
    save(plan_path, plan)
    return plan


def main(study):
    plan_path = study / "holdout_plan.json"
    plan = read(plan_path) if plan_path.exists() else register(study, plan_path)
    for record in (plan["protocol"], plan["coverage"], plan["trigger"], *plan["technical_sources"]):
        checked(record)
    output = ROOT / "user_data/crypto_quant" / study.name / "holdout_accounts"
    summaries, configs = {}, {}
    with LOCK.open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        for case in plan["candidates"]:
            role = case["name"]
            folder = output / role
            if not (folder / "result.json").is_file():
                subprocess.run([
                    sys.executable, "-B", "-m",
                    "research.crypto.strategy_comparison_wide_20261105.run_wide_comparison",
                    "--plan", str(plan_path), "--candidate", role, "--cost-bps", str(COST),
                    "--output", str(folder),
                ], cwd=ROOT, check=True)
            assert read(folder / "registered_plan.json") == plan
            assert read(folder / "fill_cash_audit.json")["passed"]
            configs[role] = read(folder / "effective_config.json")
            assert configs[role]["strategy"] == case["strategy"]
            assert configs[role]["max_open_trades"] == case["max_open_trades"]
            for filename, source in case["source_files"].items():
                checked({"path": folder / "strategies" / filename, "sha256": source["sha256"]})
            archive, = (folder / "native_results").glob("*.zip")
            stats, summary = analyze(plan, folder / "result.json", archive,
                                     folder / "hourly_equity.parquet", COST)
            if "ProfitTail.py" in case["source_files"]:
                summary["tail_callbacks"] = audit_tail(plan, folder, case["strategy"], stats["trades"])
            summaries[role] = summary
    comparison = {role: invariant_config(config) for role, config in configs.items()}
    assert comparison["original"] == comparison["fixed_boost"] == comparison["candidate"]
    returns = {role: summary["periods"][0]["net_return"] for role, summary in summaries.items()}
    pnl = {
        "accounts": summaries,
        "return_lift_over_boost": returns["candidate"] - returns["fixed_boost"],
        "return_lift_over_original": returns["candidate"] - returns["original"],
        "both_controls_exceeded": returns["candidate"] > max(
            returns["original"], returns["fixed_boost"]
        ),
        "all_accounts_reconciled": all(row["reconciliation"]["passed"] for row in summaries.values()),
        "validation_scope": "Twenty UTC days at 15bp. Limited observations remain.",
        "orders_enabled": False,
    }
    save(study / "holdout_comparison.json", pnl)
    print(returns)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=HERE)
    main(parser.parse_args().study.resolve(strict=True))
