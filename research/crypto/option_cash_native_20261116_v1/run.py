"""Actual serial isolated native processes with terminal-status receipts."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def verify_frozen():
    frozen = json.loads((HERE / "implementation_freeze.json").read_text())
    for path, expected in frozen["files"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen input/implementation drift: " + path)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    receipt = HERE / "execution_receipt.json"
    if receipt.exists():
        raise ValueError("Final processes must not overwrite earlier attempts")
    rows = []
    for currency in ["BTC", "ETH"]:
        for case in ["short", "long", "breach", "pending", "full_start"]:
            verify_frozen()
            attempt = currency + "_" + case + "_final"
            command = [
                sys.executable,
                str(HERE / ("full_start.py" if case == "full_start" else "native_probe.py")),
                "--currency",
                currency,
                "--attempt",
                attempt,
            ]
            if case != "full_start":
                command += ["--case", case]
            row = {
                "currency": currency,
                "case": case,
                "attempt": attempt,
                "command": command,
                "folder": str(HERE / "runs" / attempt),
                "started_at": datetime.now(UTC).isoformat(),
            }
            rows.append(row)
            with (HERE / (attempt + ".log")).open("x") as log:
                child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                row["pid"] = child.pid
                save(receipt, rows)
                try:
                    child.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    row["timeout"] = True
                row.update(returncode=child.returncode, ended_at=datetime.now(UTC).isoformat())
                save(receipt, rows)
            print(attempt, child.returncode, flush=True)
            if child.returncode != 0:
                raise RuntimeError("Preserved failed native attempt: " + attempt)
    verify_frozen()


if __name__ == "__main__":
    main()
