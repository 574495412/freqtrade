"""Sequential registered native wallets; never restart a live or failed attempt."""

import json
import os
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.hawkes_source_native_20261117_v1.native_runner import sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def save(name, value):
    path = HERE / name
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    frozen = json.loads((HERE / "registration_attempt2_freeze.json").read_text())
    for path, digest in frozen["files"].items():
        if sha(path) != digest:
            raise ValueError("Registered input changed: " + path)
    plan = json.loads((HERE / "plan_attempt2.json").read_text())
    env = dict(os.environ)
    env.update(
        DYLD_LIBRARY_PATH=str(
            ROOT / "research/crypto/freqai_rf_native_20261114_v1/dependencies/sklearn/.dylibs"
        ),
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
    )
    completed = []
    for candidate in plan["candidates"]:
        name, cost = candidate["name"], candidate["cost_bps"]
        output = Path(plan["output_root"]) / (name + f"_{cost}bps")
        if output.exists():
            raise ValueError("Preserve existing attempt; do not restart: " + str(output))
        command = [
            sys.executable,
            "-B",
            "-m",
            plan["runner_module"],
            "--plan",
            str(HERE / "plan_attempt2.json"),
            "--candidate",
            name,
            "--cost-bps",
            str(cost),
            "--output",
            str(output),
        ]
        with (HERE / (name + "_attempt2_execution.log")).open("x") as stream:
            process = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT
            )
            save(
                ".process.json",
                {
                    "state": "RUNNING",
                    "controller_pid": os.getpid(),
                    "native_worker_pid": process.pid,
                    "candidate": name,
                    "output": str(output),
                    "started_at": datetime.now(UTC).isoformat(),
                    "plan_sha256": sha(HERE / "plan_attempt2.json"),
                    "verified_handle": "Popen child; poll tool session or OS PID",
                    "completed_candidates": completed,
                    "orders_enabled": False,
                },
            )
            print("NATIVE_WALLET_STARTED", name, process.pid, flush=True)
            code = process.wait()
        receipt = {
            "returncode": code,
            "native_worker_pid": process.pid,
            "result_sha256": sha(output / "result.json")
            if (output / "result.json").exists()
            else None,
        }
        (output / "process_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if code != 0:
            save(
                "execution_failure_attempt2.json",
                {
                    "candidate": name,
                    "exit_code": code,
                    "output": str(output),
                    "stopped_at": datetime.now(UTC).isoformat(),
                    "completed_candidates": completed,
                    "all_failed_outputs_preserved": True,
                    "automatic_retry": False,
                    "orders_enabled": False,
                },
            )
            raise RuntimeError(f"Native wallet terminated: {name}, exit={code}")
        result = json.loads((output / "result.json").read_text())
        if (
            result["candidate"] != name
            or result["cost_bps_per_side"] != cost
            or result["initial_equity_usdt"] != candidate["capital"]
            or result["orders_enabled"]
        ):
            raise ValueError("Native result differs from registered account")
        completed.append(name)
        print("NATIVE_WALLET_TERMINAL", name, flush=True)
    save(
        "execution_complete_attempt2.json",
        {
            "completed_candidates": completed,
            "native_accounts_completed": len(completed),
            "independent_account_audit_and_comparison_pending": True,
            "goal_complete": False,
            "orders_enabled": False,
        },
    )
    save(
        ".process.json",
        {
            "state": "COMPLETED",
            "controller_pid": os.getpid(),
            "completed_candidates": completed,
            "goal_complete": False,
        },
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        save(
            ".process_terminal_failure.json",
            {
                "state": "FAILED",
                "traceback": traceback.format_exc(),
                "at": datetime.now(UTC).isoformat(),
                "goal_complete": False,
                "orders_enabled": False,
            },
        )
        raise
