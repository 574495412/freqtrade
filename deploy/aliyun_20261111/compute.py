"""Serialize heavy analysis and admit daily selection only in an idle window."""

import argparse
import fcntl
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "user_data/deployment/live-heavy-compute.lock"
MIB = 1024**2


def lock_file():
    return os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)


@contextmanager
def core_slot():
    fd = lock_file()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def selector_capacity(group=None, meminfo=Path("/proc/meminfo")):
    if group is None:
        line = next(line for line in Path("/proc/self/cgroup").read_text().splitlines()
                    if line.startswith("0::"))
        parts = Path(line[3:]).parts
        end = parts.index("freqtrade-live.slice") + 1
        group = Path("/sys/fs/cgroup").joinpath(*parts[1:end])
    if int((group / "memory.max").read_text()) != 900 * MIB:
        return False
    current = int((group / "memory.current").read_text())
    stats = {key: int(value) for key, value in
             (row.split() for row in (group / "memory.stat").read_text().splitlines())}
    # Inactive file cache is reclaimable; do not count it as anonymous headroom use.
    used = max(0, current - stats.get("inactive_file", 0))
    high = int((group / "memory.high").read_text())
    available = int(next(row.split()[1] for row in meminfo.read_text().splitlines()
                         if row.startswith("MemAvailable:"))) * 1024
    pressure = next(row for row in (group / "memory.pressure").read_text().splitlines()
                    if row.startswith("full "))
    full = float(next(item.split("=")[1] for item in pressure.split()
                      if item.startswith("avg10=")))
    return high - used >= 256 * MIB and available >= 656 * MIB and full < 1


@contextmanager
def selector_slot():
    # Preserve existing local behavior; the ECS timer is the bounded deployment.
    if ROOT != Path("/opt/freqtrade"):
        yield None
        return
    if not 75 <= time.time() % 300 < 105:
        yield "outside_idle_window"
        return
    fd = lock_file()
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield "core_analysis_busy"
            return
        if not selector_capacity():
            yield "memory_or_pressure_admission"
            return
        yield None
    finally:
        os.close(fd)


def publish_ready(strategy):
    if ROOT != Path("/opt/freqtrade"):
        return
    pairs = strategy.dp.current_whitelist()
    if not pairs:
        return
    for pair in pairs:
        raw = strategy.dp.ohlcv(pair, strategy.timeframe, copy=False)
        frame, _ = strategy.dp.get_analyzed_dataframe(pair, strategy.timeframe)
        if (
            raw.empty or len(frame) != 2
            or frame.iloc[-1]["date"] != raw.iloc[-1]["date"]
        ):
            return
    folder = Path(strategy.config["user_data_dir"])
    temporary = folder / "analysis-ready.writing"
    temporary.write_text(json.dumps({
        "pid": os.getpid(), "process_start_ticks": process_start_ticks(os.getpid()),
        "time": time.time(), "pairs": len(pairs),
    }))
    temporary.chmod(0o600)
    temporary.replace(folder / "analysis-ready.private.json")


def wait_spot(seconds=240):
    deadline = time.monotonic() + seconds
    marker = ROOT / "user_data/deployment/mainnet-100-20261109/spot/analysis-ready.private.json"
    while time.monotonic() < deadline:
        pid = int(subprocess.check_output([
            "systemctl", "show", "freqtrade-live-spot.service", "-p", "MainPID", "--value",
        ], text=True, timeout=5).strip())
        try:
            value = json.loads(marker.read_text())
        except (OSError, ValueError):
            value = {}
        if pid > 0 and value.get("pid") == pid and value.get("pairs") == 36:
            try:
                if (value.get("process_start_ticks") == process_start_ticks(pid)
                        and 0 <= time.time() - value.get("time", 0) < 30):
                    return
            except OSError:
                pass
        time.sleep(1)
    raise RuntimeError("Fresh spot native analysis required before futures initialization")


def process_start_ticks(pid):
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-spot", action="store_true", required=True)
    parser.parse_args()
    wait_spot()
