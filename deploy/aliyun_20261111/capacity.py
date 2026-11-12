"""Held ECS admission: sequential core initialization, then idle-window selection.

All exchange interaction is performed by the existing GET-only native probes.
This controller never grants admission or launches a live trading service.
"""

import hashlib
import json
import os
import pwd
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from deploy.aliyun_20261111.compute import selector_capacity
from deploy.aliyun_20261111.systemd import (
    ADMISSION,
    AGGREGATE_HIGH_MIB,
    AGGREGATE_MAX_MIB,
    REMOTE_ROOT,
    RESOURCE_LIMITS,
    require_stopped,
)
from deploy.launch import verify


GROUP = Path("/sys/fs/cgroup/freqtrade.slice/freqtrade-live.slice")
STATE = REMOTE_ROOT / "user_data/deployment"
PYTHON = REMOTE_ROOT / ".venv-paper/bin/python"
MIB = 1024**2


def utc():
    return datetime.now(UTC).isoformat()


def events():
    return {
        key: int(value)
        for key, value in (
            row.split() for row in (GROUP / "memory.events").read_text().splitlines()
        )
    }


def available():
    return (
        int(
            next(
                row.split()[1]
                for row in Path("/proc/meminfo").read_text().splitlines()
                if row.startswith("MemAvailable:")
            )
        )
        * 1024
    )


def ledger_hashes():
    result = {}
    for market in ("spot", "futures"):
        digest = hashlib.sha256()
        with (STATE / "mainnet-100-20261109" / market / "trades.sqlite").open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        result[market] = digest.hexdigest()
    return result


def failed_jobs(states):
    return {
        label: code
        for label, code in states.items()
        if code is not None
        and (
            (label == "bytecode" and code != 0)
            or (label.startswith("selector") and code not in (0, 75))
            or (label != "bytecode" and not label.startswith("selector"))
        )
    }


def main():  # noqa: C901 -- Keep the sequential admission state machine and cleanup in one scope.
    if os.geteuid() != 0 or Path.cwd().resolve() != REMOTE_ROOT:
        raise ValueError("Use the held ECS Git checkout as root")
    require_stopped()
    verify()
    if (
        ADMISSION.exists()
        or int((GROUP / "memory.max").read_text()) != AGGREGATE_MAX_MIB * MIB
        or int((GROUP / "memory.high").read_text()) != AGGREGATE_HIGH_MIB * MIB
        or list(GROUP.glob("freqtrade-sequential-*.service"))
        or list(GROUP.glob("freqtrade-admission-*.service"))
    ):
        raise ValueError("Require held services, the reviewed aggregate budget and no other probes")
    os.umask(0o077)
    stamp = str(int(time.time()))
    base = STATE / (
        "native-capacity-staggered-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    base.mkdir(mode=0o700)
    user = pwd.getpwnam("freqtrade")
    os.chown(base, user.pw_uid, user.pw_gid)
    units, jobs, logs, handles, peaks = {}, {}, {}, [], {}
    before, hashes = events(), ledger_hashes()
    started, peak, minimum = time.monotonic(), 0, available()
    core_passed, passed, reason = False, False, "deadline"
    stable, pressure_since, last_progress = None, None, 0
    selector_attempt, selector_next = 0, 0

    def launch(label, workload, body, runtime=1500, root=False, source_write=False):
        name = "freqtrade-admission-" + label + "-" + stamp
        high, maximum, swap, cpu = RESOURCE_LIMITS[workload]
        units[label] = name
        logs[label] = base / (label + ".log")
        handle = logs[label].open("w")
        handles.append(handle)
        writable = str(REMOTE_ROOT / "user_data")
        if source_write:
            writable += " " + str(REMOTE_ROOT / "deploy")
        if root:
            writable += (
                " " + str(REMOTE_ROOT / "freqtrade") + " " + str(REMOTE_ROOT / ".venv-paper")
            )
        command = [
            "systemd-run",
            "--unit=" + name,
            "--slice=freqtrade-live.slice",
            "--wait",
            "--pipe",
            "--collect",
            "-p",
            "User=" + ("root" if root else "freqtrade"),
            "-p",
            "Group=" + ("root" if root else "freqtrade"),
            "-p",
            "WorkingDirectory=" + str(REMOTE_ROOT),
            "-p",
            f"MemoryHigh={high}M",
            "-p",
            f"MemoryMax={maximum}M",
            "-p",
            f"MemorySwapMax={swap}M",
            "-p",
            f"CPUQuota={cpu}%",
            "-p",
            f"RuntimeMaxSec={runtime}s",
            "-p",
            "TimeoutStopSec=15s",
            "-p",
            "OOMPolicy=stop",
            "-p",
            "TasksMax=64",
            "-p",
            "UMask=" + ("0022" if root else "0077"),
            "-p",
            "NoNewPrivileges=yes",
            "-p",
            "PrivateTmp=yes",
            "-p",
            "ProtectSystem=strict",
            "-p",
            "ProtectHome=yes",
            "-p",
            "ReadWritePaths=" + writable,
            "--setenv=OPENBLAS_NUM_THREADS=1",
            "--setenv=OMP_NUM_THREADS=1",
            "--setenv=MKL_NUM_THREADS=1",
            "--setenv=MALLOC_ARENA_MAX=2",
            "--setenv=MALLOC_TRIM_THRESHOLD_=131072",
            str(PYTHON),
            "-B",
            *body,
        ]
        jobs[label] = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
        peaks[label] = 0

    selector_body = """
import json,sys,traceback
from deploy.aliyun_20261111.compute import selector_slot
from deploy.aliyun_20261111.native_probe import readonly_transport
try:
 with selector_slot() as reason:
  if reason:
   print(json.dumps({'deferred':reason}),flush=True);sys.exit(75)
  with readonly_transport() as counters:
   from deploy.mainnet_100_20261109.manual import verify
   from deploy.nfi80_selected.selector import main
   verify();main(force_refresh=True)
   if counters['blocked_writes']:raise ValueError('Unexpected write attempt')
   print(json.dumps({'selector_pipeline_passed':True,'transport':counters}),flush=True)
except Exception as e:
 frames=[{'file':f.filename,'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)[-6:]]
 print(json.dumps({'selector_pipeline_passed':False,'type':type(e).__name__,'frames':frames}),flush=True)
 sys.exit(1)
"""
    try:
        launch(
            "bytecode",
            "spot",
            ["-m", "deploy.aliyun_20261111.bytecode"],
            runtime=240,
            root=True,
            source_write=True,
        )
        if jobs["bytecode"].wait(timeout=260) != 0:
            raise RuntimeError("Checked runtime bytecode failed")
        launch(
            "spot",
            "spot",
            [
                "-m",
                "deploy.aliyun_20261111.native_probe",
                "--market",
                "spot",
                "--folder",
                str(base / "spot"),
                "--hold-seconds",
                "1500",
            ],
        )
        while time.monotonic() - started < 1200:
            now = time.monotonic()
            current = int((GROUP / "memory.current").read_text())
            peak, minimum = max(peak, current), min(minimum, available())
            for label, name in units.items():
                path = GROUP / (name + ".service") / "memory.current"
                if path.exists():
                    peaks[label] = max(peaks[label], int(path.read_text()))
            states = {label: process.poll() for label, process in jobs.items()}
            failed = failed_jobs(states)
            if failed:
                reason = "native_job_ended " + str(failed)
                break
            pressure = next(
                row
                for row in (GROUP / "memory.pressure").read_text().splitlines()
                if row.startswith("full ")
            )
            full = float(
                next(item.split("=")[1] for item in pressure.split() if item.startswith("avg10="))
            )
            risky = full > 25 or available() < 400 * MIB
            pressure_since = (pressure_since or now) if risky else None
            if pressure_since and now - pressure_since > 15:
                reason = "pressure_guard_aborted"
                break
            if events().get("oom_kill", 0) != before.get("oom_kill", 0):
                reason = "cgroup_oom"
                break
            if (
                int((GROUP / "memory.max").read_text()) != AGGREGATE_MAX_MIB * MIB
                or int((GROUP / "memory.high").read_text()) != AGGREGATE_HIGH_MIB * MIB
            ):
                reason = "kernel_budget_changed"
                break
            if (base / "spot/ready.json").exists() and "futures" not in jobs:
                launch(
                    "futures",
                    "futures",
                    [
                        "-m",
                        "deploy.aliyun_20261111.native_probe",
                        "--market",
                        "futures",
                        "--folder",
                        str(base / "futures"),
                        "--hold-seconds",
                        "1500",
                    ],
                )
            ready = all((base / market / "ready.json").exists() for market in ("spot", "futures"))
            if ready and not (base / "recheck").exists():
                (base / "recheck").touch()
                (base / "recheck").chmod(0o644)
            rechecked = all(
                (base / market / "rechecked.json").exists() for market in ("spot", "futures")
            )
            selector_passed = any(
                label.startswith("selector") and code == 0 for label, code in states.items()
            )
            warm = ready and rechecked and full < 1 and available() > 400 * MIB
            if core_passed:
                warm = warm and selector_passed
            stable = (stable or now) if warm else None
            if stable and now - stable >= 60:
                if core_passed:
                    passed, reason = True, "core_and_staggered_selector_passed"
                    break
                core_passed, stable = True, None
                launch(
                    "notification-reserve",
                    "notifications",
                    [
                        "-c",
                        "import time;reserve=bytearray(64*1024*1024);time.sleep(1500)",
                    ],
                )
            active_selector = any(
                label.startswith("selector") and code is None for label, code in states.items()
            )
            if (
                core_passed
                and not selector_passed
                and not active_selector
                and now > selector_next
                and 78 <= time.time() % 300 < 100
                and selector_capacity(GROUP)
            ):
                selector_attempt += 1
                launch(
                    "selector" + str(selector_attempt),
                    "selector",
                    ["-c", selector_body],
                    runtime=180,
                    source_write=True,
                )
                selector_next = now + 300
            if now - last_progress >= 2:
                report = {
                    "at_utc": utc(),
                    "elapsed_seconds": round(now - started, 1),
                    "states": states,
                    "ready": ready,
                    "rechecked": rechecked,
                    "core_passed": core_passed,
                    "selector_passed": selector_passed,
                    "group_current_bytes": current,
                    "group_peak_bytes": peak,
                    "child_peak_bytes": peaks,
                    "host_available_bytes": available(),
                    "full_pressure_avg10": full,
                }
                temporary = base / "progress.writing"
                temporary.write_text(json.dumps(report, indent=2))
                temporary.replace(base / "progress.private.json")
                last_progress = now
            time.sleep(0.2)
    finally:
        subprocess.run(
            ["systemctl", "stop", *[name + ".service" for name in units.values()]],
            check=False,
            timeout=40,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for process in jobs.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        for handle in handles:
            handle.close()
        after, unchanged = events(), ledger_hashes() == hashes
        traces = {}
        for label, path in logs.items():
            rows = []
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("{"):
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
            traces[label] = rows
        result = {
            "at_utc": utc(),
            "passed": passed
            and unchanged
            and after.get("oom_kill", 0) == before.get("oom_kill", 0),
            "reason": reason,
            "core_passed": core_passed,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "group_peak_bytes": peak,
            "child_peak_bytes": peaks,
            "minimum_host_available_bytes": minimum,
            "before_events": before,
            "after_events": after,
            "original_ledgers_unchanged": unchanged,
            "traces": traces,
            "live_workers_started": 0,
            "orders_submitted": 0,
            "private_directory": str(base),
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
        }
        (base / "capacity-result.private.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
