"""Run only the two registered, unchanged official recursive diagnostics."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from run_one import HERE, ROOT, RUNTIME, now, sha, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    args = parser.parse_args()
    registration = HERE / "recursive_registration.json"
    registered = json.loads(registration.read_text())
    row = next(r for r in registered["runs"] if r["id"] == args.id)
    plan = json.loads((HERE / "plan.json").read_text())
    with (RUNTIME / "financial_worker.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert sha(Path(__file__)) == registered["runner_sha256"]
        assert sha(HERE / "freeze.json") == registered["parent_freeze_sha256"]
        for name, digest in json.loads((HERE / "freeze.json").read_text())["files"].items():
            assert sha(ROOT / name) == digest, name
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", plan["engine_commit"], "--", "freqtrade"],
            cwd=ROOT, text=True).splitlines()
        assert all(p.startswith("freqtrade/research/") for p in changed), changed
        data = row["data_receipt"]
        assert sha(ROOT / data["path"]) == data["sha256"]
        for name, digest in json.loads((ROOT / data["path"]).read_text()).items():
            assert sha(ROOT / row["datadir"] / name) == digest, name
        output = RUNTIME / "diagnostics" / row["id"]
        output.mkdir(parents=True, exist_ok=False)
        command = row["command"]
        start = {"id": row["id"], "started_at_utc": now(), "command": command,
                 "registration_sha256": sha(registration), "runner_sha256": sha(Path(__file__)),
                 "financial_backtests_added": 0, "orders_enabled": False}
        write(output / "start.json", start)
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR") if k in os.environ}
        env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", NUMEXPR_MAX_THREADS="1")
        peak = 0
        stop = None
        began = time.monotonic()
        with (output / "native.log").open("xb") as log:
            child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            write(output / "process.json", {"pid": child.pid, "command": command, "started_at_utc": now()})
            proc = psutil.Process(child.pid)
            while child.poll() is None:
                try:
                    peak = max(peak, proc.memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                if peak > plan["budget"]["max_rss_bytes"]:
                    stop = "memory_limit"
                if time.monotonic() - began > plan["budget"]["wall_seconds_per_run"]:
                    stop = "time_limit"
                if stop:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                    break
                time.sleep(.5)
            code = child.wait()
        logtext = (output / "native.log").read_text()
        normalized = " ".join(logtext.split())
        marker = f"Checking recursive and indicator-only lookahead bias of indicators of {row['strategy']}.py took"
        complete = code == 0 and stop is None and marker in normalized
        startup_rejected = "The strategy defines invalid startup candle count of 0" in normalized
        status = "native_diagnostic_completed" if complete else "native_startup_rejected" if startup_rejected else "technical_failure"
        receipt = dict(start, finished_at_utc=now(), returncode=code, stop_reason=stop,
                       elapsed_seconds=time.monotonic()-began, peak_rss_bytes=peak,
                       status=status, diagnostic_completed=complete, economic_qualified=False,
                       artifacts={p.name: sha(p) for p in output.iterdir() if p.is_file()})
        write(output / "receipt.json", receipt)
        with (RUNTIME / "diagnostic_ledger.jsonl").open("a") as ledger:
            ledger.write(json.dumps({"id": row["id"], "status": status, "receipt": str((output / "receipt.json").relative_to(ROOT))}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        print(json.dumps({"id": row["id"], "status": status, "returncode": code}), flush=True)
        return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
