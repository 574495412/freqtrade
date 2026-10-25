"""Run six finite, independently checked phases of the registered activity study."""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.interaction_models import verify_sources


def run(plan_path, root):
    plan = read(plan_path)
    verify_sources(plan)
    if root.exists():
        raise ValueError("Preserve previous study")
    root.mkdir(parents=True)
    prefix = [sys.executable, "-u", "-m"]
    study = prefix + ["research.crypto.run_activity_absorption"]
    base = ["--plan", str(plan_path), "--root", str(root)]
    audit = prefix + [
        "research.crypto.audit_activity_absorption",
        "--plan",
        str(plan_path),
        "--panel",
        str(root / "panel"),
    ]
    phases = [
        ("prepare", study + ["prepare"] + base),
        ("audit_preparation", audit + ["--output", str(root / "preparation_audit.json")]),
        ("learn", study + ["learn"] + base),
        (
            "audit_forecasts",
            audit
            + ["--learning", str(root / "learning"), "--output", str(root / "forecast_audit.json")],
        ),
        ("evaluate", study + ["evaluate"] + base),
        (
            "audit_evaluation",
            audit
            + [
                "--learning",
                str(root / "learning"),
                "--evaluation",
                str(root / "evaluation"),
                "--output",
                str(root / "independent_audit.json"),
            ],
        ),
    ]
    completed = []
    for name, command in phases:
        print({"stage": name, "started_utc": datetime.now(UTC).isoformat()}, flush=True)
        with (root / f"{name}.log").open("x") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        completed.append(name)
        save(
            root / "completed_stage_checkpoint.json",
            {"completed_stages": completed, "plan_sha256": sha(plan_path)},
        )
        print({"completed_stage": name, "completed_utc": datetime.now(UTC).isoformat()}, flush=True)
    verify_sources(plan)
    record = read(root / "evaluation/result.json")
    save(
        root / "summary.json",
        {
            "plan_sha256": sha(plan_path),
            "completed_stages": completed,
            "unit_screen": record,
            "independent_audit_passed": read(root / "independent_audit.json")["passed"],
            "native_account_candidates": record["native_account_forecast_candidates"],
            "economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "all_six_stages_terminal": True,
            "native_candidates": record["native_account_forecast_candidates"],
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.root)
