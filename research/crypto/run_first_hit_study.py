"""Run the registered preparation, audit, forecast, evaluation and final audit in order."""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.interaction_models import verify_sources


def run(plan_path, output):
    plan = read(plan_path)
    verify_sources(plan)
    if output.exists():
        raise ValueError("Preserve existing study, including partial failures")
    output.mkdir(parents=True)
    panel, learning, evaluation = output / "panel", output / "learning", output / "evaluation"
    stages = [
        ("prepare", "prepare_first_hit_panel", ["--output", str(panel)]),
        (
            "audit_preparation",
            "audit_first_hit_learning",
            ["--panel", str(panel), "--output", str(output / "preparation_audit.json")],
        ),
        ("learn", "run_first_hit_learning", ["--panel", str(panel), "--output", str(learning)]),
        (
            "audit_forecasts",
            "audit_first_hit_learning",
            [
                "--panel",
                str(panel),
                "--learning",
                str(learning),
                "--output",
                str(output / "forecast_audit.json"),
            ],
        ),
        (
            "evaluate",
            "screen_first_hit_learning",
            ["--panel", str(panel), "--learning", str(learning), "--output", str(evaluation)],
        ),
        (
            "audit_evaluation",
            "audit_first_hit_learning",
            [
                "--panel",
                str(panel),
                "--learning",
                str(learning),
                "--evaluation",
                str(evaluation),
                "--output",
                str(output / "independent_audit.json"),
            ],
        ),
    ]
    completed = []
    for name, module, arguments in stages:
        print({"stage_started": name, "utc": datetime.now(UTC).isoformat()}, flush=True)
        command = [
            sys.executable,
            "-u",
            "-m",
            f"research.crypto.{module}",
            "--plan",
            str(plan_path),
            *arguments,
        ]
        try:
            with (output / f"{name}.log").open("w") as stream:
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
        except subprocess.CalledProcessError as error:
            save(
                output / "failed_stage.json",
                {
                    "stage": name,
                    "returncode": error.returncode,
                    "completed_stages": completed,
                    "plan_sha256": sha(plan_path),
                    "orders_enabled": False,
                    "production_eligible": False,
                },
            )
            raise
        completed.append(name)
        save(
            output / "completed_stage_checkpoint.json",
            {"completed_stages": completed, "plan_sha256": sha(plan_path), "orders_enabled": False},
        )
        print({"stage_completed": name, "utc": datetime.now(UTC).isoformat()}, flush=True)
    result, review = read(evaluation / "result.json"), read(output / "independent_audit.json")
    save(
        output / "summary.json",
        {
            "plan_sha256": sha(plan_path),
            "completed_stages": completed,
            "unit_screen": result,
            "independent_audit_passed": review["passed"],
            "native_account_candidates": result["forecast_candidates_eligible_for_native_account"],
            "economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "all_stages_terminal": True,
            "native_account_candidates": result["forecast_candidates_eligible_for_native_account"],
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.absolute(), args.output.absolute())
