"""Bounded real child processes with terminal status; no detached research job."""

import shutil
import subprocess
import sys
from datetime import UTC, datetime

from bootstrap import HERE, ROOT, save
from native import require_freeze


def main():
    target = HERE / "execution_receipt.json"
    if target.exists():
        raise ValueError("Prior native attempts must remain intact")
    if shutil.disk_usage(ROOT).free < 536870912:
        raise RuntimeError("Diagnostic resource floor unmet")
    rows = []
    for name in ["native.py", "predictive.py", "audit.py"]:
        require_freeze()
        command = [sys.executable, str(HERE / name)]
        before = datetime.now(UTC).isoformat()
        with (HERE / (name[:-3] + ".log")).open("x") as logfile:
            child = subprocess.Popen(command, cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
            row = {"command": command, "pid": child.pid, "started_at": before}
            rows.append(row)
            save(target, rows)
            try:
                child.wait(timeout=600)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                row["timeout"] = True
            row.update(returncode=child.returncode, ended_at=datetime.now(UTC).isoformat())
            save(target, rows)
        print(name, row["returncode"], flush=True)
        if child.returncode != 0:
            raise RuntimeError(f"Preserved failed native child: {name}")
    require_freeze()


if __name__ == "__main__":
    main()
