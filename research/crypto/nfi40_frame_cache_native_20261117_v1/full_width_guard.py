"""Supervise only our new nonfinancial worker, leaving existing processes alone."""

import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    protocol = json.loads((HERE / "full_width_protocol.json").read_text())
    frozen = json.loads((HERE / "full_width_freeze.json").read_text())
    for path, expected in frozen["files"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen input changed: {path}")
    limits = protocol["resource_limits"]
    initial = psutil.virtual_memory()._asdict()
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "initial_system_memory": initial,
        "resource_limits": limits,
        "financial_accounts": 0,
        "existing_processes_modified": False,
        "samples": [],
    }
    if initial["available"] < limits["min_available_bytes"] + limits["startup_headroom_bytes"]:
        result.update(status="NOT_FEASIBLE_INITIAL_MEMORY", child_started=False)
        write(HERE / "full_width_guard_result.json", result)
        print(json.dumps(result), flush=True)
        return 3
    with (HERE / "full_width_worker.log").open("x") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "research.crypto.nfi40_frame_cache_native_20261117_v1.full_width_probe",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        process = psutil.Process(child.pid)
        result.update(child_pid=child.pid, child_started=True)
        peak = 0
        minimum_available = initial["available"]
        reason = None
        started = time.monotonic()
        count = 0
        while child.poll() is None:
            try:
                rss = process.memory_info().rss
                available = psutil.virtual_memory().available
            except psutil.NoSuchProcess:
                break
            peak = max(peak, rss)
            minimum_available = min(minimum_available, available)
            sample = {
                "seconds": time.monotonic() - started,
                "rss_bytes": rss,
                "available_bytes": available,
            }
            if count % 20 == 0:
                result["samples"].append(sample)
            count += 1
            if rss > limits["max_rss_bytes"]:
                reason = "RSS_LIMIT_EXCEEDED"
            elif available < limits["min_available_bytes"]:
                reason = "SYSTEM_AVAILABLE_MEMORY_FLOOR"
            elif time.monotonic() - started > limits["max_seconds"]:
                reason = "WALL_TIME_LIMIT"
            if reason:
                result["samples"].append(sample)
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                break
            time.sleep(limits["poll_seconds"])
        code = child.wait()
    result.update(
        status="ABORTED_RESOURCE_GAP" if reason else ("COMPLETED" if code == 0 else "FAILED"),
        abort_reason=reason,
        child_exit_code=code,
        peak_sampled_rss_bytes=peak,
        minimum_available_bytes=minimum_available,
        elapsed_seconds=time.monotonic() - started,
        completed_at=datetime.now(UTC).isoformat(),
        full40_performance_proven=False,
        execution_replacement_admitted=False,
    )
    write(HERE / "full_width_guard_result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}), flush=True)
    return 0 if result["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    sys.exit(main())
