"""Run a frozen wide-comparison plan one account at a time."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def run(plan_path, output):
    plan = json.loads(plan_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    (output / "registered_plan.json").write_bytes(plan_path.read_bytes())
    runner = plan_path.parent / "run_wide_comparison.py"
    jobs = [(c["name"], cost) for c in plan["candidates"] for cost in plan["total_cost_bps_per_side"]]
    for index, (name, cost) in enumerate(jobs, 1):
        label = f"{name}-cost{cost}"
        command = [sys.executable, str(runner), "--plan", str(plan_path), "--candidate", name,
                   "--cost-bps", str(cost), "--output", str(output / label)]
        with (output / (label + ".log")).open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            state = {"index": index, "total": len(jobs), "candidate": name, "cost": cost,
                     "pid": process.pid, "command": command, "state": "RUNNING",
                     "started_at": datetime.now(timezone.utc).isoformat()}
            save(output / "process.json", state)
            print(json.dumps(state), flush=True)
            code = process.wait()
        state.update(state="COMPLETED" if code == 0 else "FAILED", exit_code=code,
                     finished_at=datetime.now(timezone.utc).isoformat())
        save(output / "process.json", state)
        print(json.dumps(state), flush=True)
        if code:
            raise SystemExit(code)
    save(output / "completion.json", {"accounts_completed": len(jobs), "orders_enabled": False,
                                      "production_eligible": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve())
