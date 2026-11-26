"""Register and run the fixed composition through the pinned Freqtrade runner."""

import argparse
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_NB, flock
import json
from pathlib import Path
from shutil import disk_usage

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked, run, save, sha,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUTPUT = ROOT / "user_data/crypto_quant" / HERE.name
CONTROL = OUTPUT.parent / "nfi_strength_allocation_20261107_v001"
SOURCE = HERE.parent / "nfi_profit_tail_20261107"
STORAGE = HERE.parent / "nfi40_eight_slots_20261125_v1"
LOCK = HERE.parent / "nfi40_allocation_native_20261117_v1/financial_worker.lock"
NAME = "NFIBoostProfitTail"
ACCOUNT_KEYS = (
    "allowed_leverage", "data_receipts", "datadir", "end", "engine_commit",
    "engine_manifest", "engine_source", "environment", "initial_equity_usdt",
    "market_snapshot", "marks", "memory_guard_bytes", "observer_source", "pairs",
    "periods", "research_support", "runner_sha256", "start", "wall_guard_seconds",
)


def read(path):
    return json.loads(path.read_text())


def receipt(path):
    return {"path": str(path), "sha256": sha(path)}


def register():
    plan_path = HERE / "plan.json"
    if plan_path.exists():
        raise FileExistsError(plan_path)
    parent_path = CONTROL / "FlatTopBoost_15bps/registered_plan.json"
    parent = read(parent_path)
    incumbent = next(row for row in parent["candidates"] if row["strategy"] == "FlatTopBoost")
    config = read(checked(incumbent["config"]))
    assert config["max_open_trades"] == 6 and config["dry_run"]
    config["strategy"] = NAME
    config_path = HERE / "config.json"
    save(config_path, config)
    source = next(row for row in read(SOURCE / "protocol.json")["sources"]
                  if Path(row["path"]).name == "ProfitTail.py")
    checked(source)
    storage_freeze = read(STORAGE / "working_set_freeze.json")
    storage = next(row for row in storage_freeze["files"]
                   if Path(row["path"]) == STORAGE / "storage.py")
    for row in storage_freeze["files"]:
        checked(row)
    frozen_controls = {}
    for cost in (15, 8):
        control = CONTROL / f"FlatTopBoost_{cost}bps"
        archive, = (control / "native_results").glob("*.zip")
        frozen_controls[str(cost)] = [receipt(file) for file in (
            control / "result.json", archive, control / "hourly_equity.parquet",
            control / "effective_config.json",
        )]
    plan = {key: parent[key] for key in ACCOUNT_KEYS}
    plan.update(
        total_cost_bps_per_side=[15, 8], storage_source=storage,
        controls=frozen_controls, protocol=receipt(HERE / "protocol.json"),
        parent_plan=receipt(parent_path), orders_enabled=False, production_eligible=False,
        registered_at=datetime.now(timezone.utc).isoformat(),
        technical_sources=[row for row in storage_freeze["files"] if row["path"].endswith(".py")],
    )
    plan["candidates"] = [{
        **incumbent, "name": NAME, "strategy": NAME, "config": receipt(config_path),
        "source_files": {
            **incumbent["source_files"], "ProfitTail.py": source,
            f"{NAME}.py": receipt(HERE / f"{NAME}.py"),
        },
        "source_and_risk_behavior": "Fixed first stake with the pre-existing FlatProfitTail policy.",
    }]
    save(plan_path, plan)
    return plan_path


def execute(cost):
    plan_path = HERE / "plan.json"
    plan = read(plan_path)
    checked(plan["protocol"])
    checked(plan["parent_plan"])
    for record in (*plan["technical_sources"], *plan["controls"][str(cost)]):
        checked(record)
    if cost == 8 and not read(HERE / "comparison_15bp.json")["advance"]:
        raise ValueError("15bp account did not meet the frozen advance rule")
    if disk_usage(ROOT).free < 12 * 1024**3:
        raise RuntimeError("Insufficient free space for the regenerable lossless cache")
    with LOCK.open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        run(plan_path, NAME, cost, OUTPUT / f"cost_{cost}bp")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("register", "run"))
    parser.add_argument("--cost", type=int, choices=(8, 15), default=15)
    args = parser.parse_args()
    if args.operation == "register":
        print(register())
    else:
        execute(args.cost)
