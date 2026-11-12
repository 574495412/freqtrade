"""Bounded systemd definitions; importing this module loads no trading engine."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path


REMOTE_ROOT = Path("/opt/freqtrade")
SYSTEMD_DIRECTORY = Path("/etc/systemd/system")
ADMISSION = Path("/run/freqtrade-live-admitted")
SERVICES = tuple(f"freqtrade-live-{name}.service" for name in (
    "spot", "futures", "notifications", "selector",
))


def slice_unit():
    """An aggregate ceiling, independent of the sum of child ceilings."""
    return """[Unit]
Description=Aggregate Freqtrade host resource protection
[Slice]
MemoryAccounting=true
CPUAccounting=true
MemoryHigh=768M
MemoryMax=900M
MemorySwapMax=256M
CPUQuota=100%
TasksMax=192
"""


def hold():
    """Prevent future starts before requesting a graceful, nonblocking stop."""
    if os.geteuid() != 0:
        raise ValueError("Run as root")
    ADMISSION.unlink(missing_ok=True)
    # Drop-ins also protect the already deployed units, before a Git update.
    for name in SERVICES:
        folder = SYSTEMD_DIRECTORY / (name + ".d")
        folder.mkdir(exist_ok=True, mode=0o755)
        target = folder / "90-incident-hold.conf"
        target.write_text(f"[Unit]\nConditionPathExists={ADMISSION}\n[Service]\nRestart=no\n")
        target.chmod(0o644)
    failures = []
    for command in (
        ["systemctl", "daemon-reload"],
        ["systemctl", "disable", *SERVICES, "freqtrade-live-selector.timer"],
        ["systemctl", "stop", "--no-block", "freqtrade-live-selector.timer", *SERVICES],
    ):
        try:
            subprocess.run(command, check=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            # A reload/disable failure must not prevent the stop attempt.
            failures.append(command[1])
    if failures:
        raise RuntimeError(f"Incomplete hold; verify remotely: {failures}")
    print("HOLD_INSTALLED_STOP_REQUESTED: verify MainPID=0; stop is asynchronous")


def require_stopped():
    """Never move running workers into a new, lower aggregate cgroup."""
    for name in (*SERVICES, "freqtrade-live-selector.timer"):
        value = subprocess.check_output(
            ["systemctl", "show", name, "-p", "ActiveState", "--value"],
            text=True, timeout=5,
        ).strip()
        if value not in ("inactive", "failed"):
            raise ValueError(f"Stop {name} before installing aggregate protection")
    if ADMISSION.exists():
        raise ValueError("Remove runtime admission before installing protection")


def compact_cache(path):
    """Reclaim free SQLite pages while preserving every logical row and schema."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Cache database cannot be a symlink")
    before_size = path.stat().st_size
    backup = path.with_name(path.name + ".before-compact-" + str(time.time_ns()))

    def digest(conn):
        result = hashlib.sha256()
        for line in conn.iterdump():
            result.update(line.encode())
            result.update(b"\n")
        return result.hexdigest()

    with sqlite3.connect(path, timeout=5) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Cache integrity check failed")
        before = digest(conn)
        with sqlite3.connect(backup) as saved:
            conn.backup(saved)
        backup.chmod(0o600)
        conn.execute("VACUUM")
        after = digest(conn)
        if after != before or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Cache compaction changed logical data; preserve backup")
    return {
        "backup": str(backup), "before_bytes": before_size,
        "after_bytes": path.stat().st_size, "logical_sha256": after,
        "all_rows_and_schema_preserved": True,
    }


def unit(name, module, args="", root=REMOTE_ROOT):
    selector = name == "selector"
    extra = f" {root}/deploy/nfi80_selected" if selector else ""
    # Child limits alone do not protect a shared host. Every process must also
    # belong to the aggregate slice and remain held until reviewed admission.
    high, maximum, swap, cpu = {
        "spot": (600, 650, 256, 60),
        "futures": (575, 600, 256, 60),
        "notifications": (80, 120, 64, 10),
        "selector": (400, 450, 128, 40),
    }[name]
    return f"""[Unit]
Description=Existing Freqtrade live {name}
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=900
StartLimitBurst=3
ConditionPathExists={ADMISSION}

[Service]
Type=simple
Slice=freqtrade-live.slice
User=freqtrade
Group=freqtrade
WorkingDirectory={root}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=TZ=Asia/Shanghai
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=1
Environment=MKL_NUM_THREADS=1
ExecStart={root}/.venv-paper/bin/python -B -m {module} {args}
Restart=no
RestartSec=180
MemoryAccounting=true
CPUAccounting=true
MemoryHigh={high}M
MemoryMax={maximum}M
MemorySwapMax={swap}M
CPUQuota={cpu}%
TasksMax=64
OOMPolicy=stop
TimeoutStopSec=60
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={root}/user_data{extra}

[Install]
WantedBy=multi-user.target
"""


def apply():
    """Install aggregate protection only while every live unit/timer is stopped."""
    if os.geteuid() != 0 or Path.cwd().resolve() != REMOTE_ROOT:
        raise ValueError("Run as root in the existing /opt/freqtrade Git checkout")
    require_stopped()
    os.umask(0o077)
    state = REMOTE_ROOT / "user_data/deployment"
    receipt = state / ("resource-protection-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    receipt.mkdir(mode=0o700)
    modules = {
        "spot": ("deploy.mainnet_100_20261109.manual", "--worker spot"),
        "futures": ("deploy.mainnet_100_20261109.manual", "--worker futures"),
        "notifications": ("deploy.live_prepare_20261109.notify_relay", ""),
        "selector": ("deploy.mainnet_100_20261109.live_selector", ""),
    }
    aggregate = receipt / "freqtrade-live.slice"
    aggregate.write_text(slice_unit())
    candidates = [aggregate]
    for name, (module, args) in modules.items():
        candidate = receipt / f"freqtrade-live-{name}.service"
        candidate.write_text(unit(name, module, args))
        candidates.append(candidate)
    subprocess.run(["systemd-analyze", "verify", *map(str, candidates)], check=True, timeout=20)
    for candidate in candidates:
        target = SYSTEMD_DIRECTORY / candidate.name
        if target.is_symlink():
            raise ValueError("Refusing a symlink unit")
        if target.exists():
            (receipt / (candidate.name + ".before")).write_bytes(target.read_bytes())
        temporary = target.with_name(target.name + ".new")
        temporary.write_bytes(candidate.read_bytes())
        temporary.chmod(0o644)
        temporary.replace(target)
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=20)
    # Native initialize() creates the same additive index; preserve all rows.
    started = time.monotonic()
    with sqlite3.connect(state / "binance-request-guard.sqlite", timeout=5) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() - started > 30), 1000)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS public_cache_expiry ON public_cache(expires,lease_until)"
        )
    result = {
        "status": "PROTECTION_INSTALLED_STARTUP_HELD_NOT_ADMITTED",
        "units": [p.name for p in candidates],
        "cache_index_added": True,
        "workers_started": 0,
        "ledger_or_financial_config_changed": False,
    }
    (receipt / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({**result, "receipt": str(receipt)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--hold", action="store_true")
    parser.add_argument("--compact-cache", action="store_true")
    args = parser.parse_args()
    if args.hold:
        if args.compact_cache:
            parser.error("--compact-cache requires --apply")
        hold()
        raise SystemExit(0)
    if os.geteuid() != 0 or Path.cwd().resolve() != REMOTE_ROOT:
        raise ValueError("Run as root in the existing /opt/freqtrade Git checkout")
    if args.compact_cache:
        for name in ("spot", "futures", "selector"):
            value = subprocess.check_output(
                ["systemctl", "show", f"freqtrade-live-{name}.service", "-p", "MainPID", "--value"],
                text=True, timeout=5,
            ).strip()
            if value != "0":
                raise ValueError("Stop trading workers and selector before compaction")
        cache = REMOTE_ROOT / "user_data/deployment/binance-request-guard.sqlite"
        print(json.dumps(compact_cache(cache)))
    apply()

