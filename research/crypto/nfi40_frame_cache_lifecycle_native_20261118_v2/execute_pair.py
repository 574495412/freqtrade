"""Future root-gated serial lifecycle controller; never retries a failed variant."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from .admission import checked, read, require_release, sha
from .native_producer import HERE, ROOT, VARIANTS, save
from .receipt_contract import compare_receipts
from .technical_contract import observed_storage_coverage, verify_terminal_resources


def now():
    return datetime.now(UTC).isoformat()


def terminal_copy(output, process, plan):
    """Only an observed Popen.wait return code can complete the worker receipt."""
    path = output / "native_lifecycle_receipt.json"
    if process.get("terminal_witness") != "Popen.wait" or process.get("returncode") != 0:
        raise ValueError("Worker did not observably exit successfully")
    if process.get("receipt_sha256") != sha(path):
        raise ValueError("Terminal process receipt does not bind native output")
    receipt = read(path)
    if receipt.get("terminal_exit_code") is not None or not receipt.get("worker_method_completed"):
        raise ValueError("Worker attempted to claim its own future terminal state")
    checked(receipt["native_zip"])
    checked(receipt["full_semantic_trace"])
    verify_terminal_resources(output, process, plan["resource_limits"])
    return {**receipt, "terminal_exit_code": process["returncode"]}


def run():
    plan = require_release()  # No process/output/lock exists before the sealed gate.
    output_root = ROOT / "user_data/crypto_quant" / HERE.name
    control = output_root / "controller"
    control.mkdir(parents=True, exist_ok=False)
    results = []
    try:
        for variant in VARIANTS:
            require_release()
            output = output_root / variant
            if output.exists():
                raise ValueError("Existing attempt cannot be reused or overwritten: " + variant)
            command = [
                sys.executable,
                "-B",
                "-m",
                "research.crypto." + HERE.name + ".native_producer",
                "--variant",
                variant,
            ]
            started = now()
            with (control / (variant + ".log")).open("x") as log:
                process = subprocess.Popen(
                    command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
                )
                identity = {
                    "pid": process.pid,
                    "create_time": psutil.Process(process.pid).create_time(),
                }
                save(
                    control / (variant + ".running.json"),
                    {**identity, "command": command, "started_at": started},
                )
                deadline = (
                    time.monotonic() + plan["resource_limits"]["max_seconds_per_variant"] + 30
                )
                controller_terminated = False
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.5)
                if process.poll() is None:
                    # Terminate only this controller-owned new process group. A timeout
                    # is not terminal evidence: wait observes the actual subsequent exit.
                    controller_terminated = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
            receipt_path = output / "native_lifecycle_receipt.json"
            resource_path = output / "resources.json"
            terminal = {
                **identity,
                "command": command,
                "started_at": started,
                "ended_at": now(),
                "returncode": returncode,
                "terminal_witness": "Popen.wait",
                "controller_terminated": controller_terminated,
                "receipt_sha256": sha(receipt_path) if receipt_path.is_file() else None,
                "resources_sha256": sha(resource_path) if resource_path.is_file() else None,
                "producer_freeze_sha256": sha(HERE / "producer_freeze.json"),
                "plan_sha256": sha(HERE / "plan.json"),
                "orders_enabled": False,
            }
            save(control / (variant + ".process_receipt.json"), terminal)
            if returncode != 0 or controller_terminated:
                raise RuntimeError("Native variant failed; no retry: " + variant)
            results.append(terminal_copy(output, terminal, plan))
        result = compare_receipts(*results)
        if (
            results[0]["full_semantic_trace"]["sha256"]
            != results[1]["full_semantic_trace"]["sha256"]
        ):
            raise ValueError("Full exact semantic trace differs")
        save(control / "observed_native_parity.json", result)
        coverage = observed_storage_coverage(*results)
        save(
            control / "comparison.json",
            {**result, **coverage, "native_variants_completed": 2, "orders_enabled": False},
        )
        return result
    except BaseException as error:
        save(
            control / "not_admitted.json",
            {
                "status": "NOT_ADMITTED",
                "error": str(error),
                "completed_variants": len(results),
                "retry": False,
                "orders_enabled": False,
            },
        )
        raise


if __name__ == "__main__":
    print(json.dumps(run()))
