"""Sequential supervisor for the registered native account comparison."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def run(plan, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / "registered_plan.json").write_bytes(plan.read_bytes())
    jobs = [(name, cost) for name in ["trend-control", "donchian20-half-risk", "nfi-x7-1x",
                                     "nfi-x4-compact-1x", "nfi-x8-1x"] for cost in [8, 15, 5]]
    for index, (name, cost) in enumerate(jobs):
        command = [sys.executable, str(Path(__file__).with_name("run_matched_comparison.py")),
                   "--plan", str(plan), "--candidate", name, "--cost-bps", str(cost),
                   "--output", str(output / f"{name}-cost{cost}")]
        with (output / f"{name}-cost{cost}.log").open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            status = {"index": index, "total": len(jobs), "candidate": name, "cost": cost,
                      "pid": process.pid, "command": command, "state": "RUNNING",
                      "started_at": datetime.now(timezone.utc).isoformat()}
            (output / "process.json").write_text(json.dumps(status, indent=2) + "\n")
            print(json.dumps(status), flush=True)
            code = process.wait()
        status.update(state="COMPLETED" if code == 0 else "FAILED", exit_code=code,
                      finished_at=datetime.now(timezone.utc).isoformat())
        (output / "process.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        if code:
            raise SystemExit(code)
    (output / "completion.json").write_text(json.dumps({"accounts_completed": len(jobs),
                                                        "orders_enabled": False,
                                                        "production_eligible": False}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve())
