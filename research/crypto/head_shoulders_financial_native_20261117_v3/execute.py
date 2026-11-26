"""Run four root-released native wallets serially, retaining every attempt."""

import json
import os
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .release_guard import require_release, sha


HERE = Path(__file__).parent.resolve()
ROOT = HERE.parents[2]


def save(name, value):
    path = HERE / name
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    for path, digest in json.loads((HERE / "implementation_freeze.json").read_text())[
        "files"
    ].items():
        if sha(path) != digest:
            raise ValueError("Registered financial implementation changed: " + path)
    plan_path = HERE / "plan.json"
    require_release(plan_path)
    plan = json.loads(plan_path.read_text())
    completed = []
    for candidate in plan["candidates"]:
        output = Path(plan["output_root"]) / candidate["name"]
        if output.exists():
            raise ValueError("Preserve prior attempt, no automatic restart: " + str(output))
        command = [
            sys.executable,
            "-B",
            "-m",
            plan["runner_module"],
            "--plan",
            str(plan_path),
            "--candidate",
            candidate["name"],
            "--cost-bps",
            str(candidate["cost_bps"]),
            "--output",
            str(output),
        ]
        env = dict(os.environ)
        env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        with (HERE / (candidate["name"] + "_execution.log")).open("x") as stream:
            process = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT
            )
            state = {
                "state": "RUNNING",
                "controller_pid": os.getpid(),
                "native_worker_pid": process.pid,
                "candidate": candidate["name"],
                "output": str(output),
                "started_at": datetime.now(UTC).isoformat(),
                "plan_sha256": sha(plan_path),
                "completed": completed,
                "orders_enabled": False,
            }
            save(".process.json", state)
            print(json.dumps(state), flush=True)
            code = process.wait()
        receipt = {
            "returncode": code,
            "native_worker_pid": process.pid,
            "result_sha256": sha(output / "result.json")
            if (output / "result.json").exists()
            else None,
        }
        if output.exists():
            (output / "process_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if code != 0:
            save(
                "execution_failure.json",
                {
                    "candidate": candidate["name"],
                    "exit_code": code,
                    "output": str(output),
                    "completed": completed,
                    "automatic_retry": False,
                    "all_failed_outputs_preserved": True,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
            raise RuntimeError(f"Native wallet failed: {candidate['name']}, exit={code}")
        result = json.loads((output / "result.json").read_text())
        if (
            result["candidate"] != candidate["name"]
            or result["cost_bps_per_side"] != candidate["cost_bps"]
            or result["initial_equity_usdt"] != candidate["capital"]
            or result["source_callback_errors"] != 0
            or result["orders_enabled"]
        ):
            raise ValueError("Native result contract mismatch")
        completed.append(candidate["name"])
        print("NATIVE_WALLET_COMPLETED", candidate["name"], flush=True)
    save(
        "execution_complete.json",
        {
            "completed": completed,
            "native_accounts_completed": len(completed),
            "independent_account_and_funding_audit_pending": True,
            "orders_enabled": False,
            "goal_complete": False,
        },
    )
    save(
        ".process.json",
        {
            "state": "COMPLETED",
            "controller_pid": os.getpid(),
            "completed": completed,
            "orders_enabled": False,
        },
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        save(
            "execution_terminal_failure.json",
            {
                "state": "FAILED",
                "at": datetime.now(UTC).isoformat(),
                "traceback": traceback.format_exc(),
                "orders_enabled": False,
                "goal_complete": False,
            },
        )
        raise
