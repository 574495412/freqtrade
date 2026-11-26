"""Run each frozen capital and cost combination serially after the active queue."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .run import HERE, PARENT, ROOT, read, save, sha, verify_study


def active_head_shoulders_controller():
    state = read(PARENT / ".process.json")
    pid = state["controller_pid"]
    try:
        process = psutil.Process(pid)
        command = process.cmdline()
    except psutil.NoSuchProcess:
        return None
    if "research.crypto.head_shoulders_financial_native_20261117_v3.execute" not in command:
        return None
    return process if process.is_running() else None


def main():
    verify_study()
    path = HERE / "execution_state.json"
    if path.exists():
        raise FileExistsError(path)
    save(path, {"state": "WAITING_FOR_CURRENT_NATIVE_QUEUE", "controller_pid": os.getpid()})
    active = active_head_shoulders_controller()
    if active is not None:
        print(f"Waiting for the already running H&S queue, PID {active.pid}", flush=True)
        while active.is_running():
            time.sleep(5)
    prior_state = read(PARENT / ".process.json")
    if prior_state["state"] != "COMPLETED" and not (PARENT / "execution_terminal_failure.json").is_file():
        save(path, {"state": "HELD", "reason": "Prior queue lacks a terminal receipt"})
        raise ValueError("Prior native queue terminal state is unverified")
    output_root = ROOT / "user_data/crypto_quant" / HERE.name
    completed = []
    for capital, cost in ((1000, 15), (1000, 8), (500, 15), (500, 8)):
        name = f"dc60_{capital}_cost{cost}"
        output = output_root / name
        command = [
            sys.executable, "-B", "-m", f"research.crypto.{HERE.name}.run",
            "--capital", str(capital), "--cost", str(cost),
        ]
        with (HERE / f"{name}.log").open("x") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            running = {
                "state": "RUNNING", "controller_pid": os.getpid(),
                "native_worker_pid": process.pid, "candidate": name, "completed": completed,
                "freeze_sha256": sha(HERE / "freeze.json"), "orders_enabled": False,
            }
            save(path, running)
            print(json.dumps(running), flush=True)
            code = process.wait()
        result = output / "result.json"
        receipt = {
            "returncode": code, "native_worker_pid": process.pid,
            "result_sha256": sha(result) if result.is_file() else None,
        }
        if output.is_dir():
            save(output / "process_receipt.json", receipt)
        if code or not result.is_file():
            save(path, {"state": "FAILED", "candidate": name, "completed": completed, **receipt})
            raise RuntimeError(f"{name} ended with exit {code}")
        completed.append(name)
    save(path, {"state": "COMPLETED", "controller_pid": os.getpid(), "completed": completed})


if __name__ == "__main__":
    main()
