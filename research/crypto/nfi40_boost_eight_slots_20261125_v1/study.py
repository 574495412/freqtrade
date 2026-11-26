"""Register, run, and reconcile the second NFI composition."""

import argparse
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from shutil import disk_usage

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import (
    HERE as FIRST, LOCK, ROOT, STORAGE, read, receipt,
)
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked, run, save,
)


HERE = Path(__file__).resolve().parent
PLAN = HERE / "plan.json"
OUTPUT = ROOT / "user_data/crypto_quant" / HERE.name
NAME = "NFIBoostEightSlots"


def register():
    if PLAN.exists():
        raise FileExistsError(PLAN)
    parent = read(FIRST / "plan.json")
    candidate = parent["candidates"][0]
    config_path = HERE / "config.json"
    config = read(checked(candidate["config"]))
    config.update(strategy=NAME, max_open_trades=8)
    save(config_path, config)
    body = next(row for row in read(STORAGE / "freeze.json")["files"]
                if Path(row["path"]).name == "NFIEightSlots.py")
    checked(body)
    case = {
        **candidate, "name": NAME, "strategy": NAME, "config": receipt(config_path),
        "max_open_trades": 8,
        "source_files": {
            "NostalgiaForInfinityX8.py": candidate["source_files"]["NostalgiaForInfinityX8.py"],
            "StrengthAllocation.py": candidate["source_files"]["StrengthAllocation.py"],
            "NFIEightSlots.py": body,
            f"{NAME}.py": receipt(HERE / f"{NAME}.py"),
        },
        "source_and_risk_behavior": "Fixed first stake plus the exact eight-slot wallet rule.",
    }
    validation = ROOT / "user_data/crypto_quant" / FIRST.name / "holdout_inputs/coverage.json"
    assert not read(validation)["errors"]
    save(PLAN, {
        **parent, "candidates": [case], "protocol": receipt(HERE / "protocol.json"),
        "prior_evidence": [receipt(FIRST / "comparison_15bp.json"),
                           receipt(STORAGE / "comparison_15bp.json")],
        "independent_input_coverage": receipt(validation),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    })


def verify_plan(plan, cost):
    for record in (
        plan["protocol"], plan["independent_input_coverage"], *plan["prior_evidence"],
        *plan["technical_sources"], *plan["controls"][str(cost)],
    ):
        checked(record)


def execute(cost):
    plan = read(PLAN)
    verify_plan(plan, cost)
    if cost == 8 and not read(HERE / "comparison_15bp.json")["advance"]:
        raise ValueError("15bp account did not meet the frozen advance rule")
    if disk_usage(ROOT).free < 12 * 1024**3:
        raise RuntimeError("Insufficient free space for the regenerable lossless cache")
    with LOCK.open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        run(PLAN, NAME, cost, OUTPUT / f"cost_{cost}bp")


def audit(cost):
    import pandas as pd

    from research.crypto.nfi40_boost_profit_tail_20261125_v1.audit import (
        analyze, compare_trade_counts,
    )

    plan = read(PLAN)
    verify_plan(plan, cost)
    folder = OUTPUT / f"cost_{cost}bp"
    assert read(folder / "registered_plan.json") == plan
    assert read(folder / "fill_cash_audit.json")["passed"]
    fixed_result, fixed_archive, fixed_curve, fixed_config = map(
        checked, plan["controls"][str(cost)]
    )
    config, fixed = read(folder / "effective_config.json"), read(fixed_config)
    assert (config["strategy"], fixed["strategy"]) == (NAME, "FlatTopBoost")
    assert (config["max_open_trades"], fixed["max_open_trades"]) == (8, 6)
    for key in (
        "timeframe", "dry_run_wallet", "stake_amount", "tradable_balance_ratio",
        "trading_mode", "margin_mode", "order_types", "fee", "timerange",
        "datadir", "pairlists", "exchange",
    ):
        assert config[key] == fixed[key], key
    for name, row in plan["candidates"][0]["source_files"].items():
        checked({"path": folder / "strategies" / name, "sha256": row["sha256"]})
    archive, = (folder / "native_results").glob("*.zip")
    new_stats, candidate = analyze(plan, folder / "result.json", archive,
                                   folder / "hourly_equity.parquet", cost)
    boost_stats, boost = analyze(plan, fixed_result, fixed_archive, fixed_curve, cost)
    cutoff = int(pd.Timestamp(plan["periods"][0]["end"]).timestamp() * 1000)
    gain = candidate["periods"][0]["net_return"] - boost["periods"][0]["net_return"]
    verdict = {
        "cost_bps_per_side": cost, "fixed_boost": boost, "candidate": candidate,
        "return_lift_over_boost": gain,
        "counts": compare_trade_counts(boost_stats, new_stats, cutoff),
        "advance": gain > 0 and candidate["reconciliation"]["passed"] and boost["reconciliation"]["passed"],
        "orders_enabled": False,
    }
    save(HERE / f"comparison_{cost}bp.json", verdict)
    print({"return_lift_over_boost": gain, "advance": verdict["advance"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("register", "run", "audit"))
    parser.add_argument("--cost", type=int, choices=(8, 15), default=15)
    args = parser.parse_args()
    if args.operation == "register":
        register()
    elif args.operation == "run":
        execute(args.cost)
    else:
        audit(args.cost)
