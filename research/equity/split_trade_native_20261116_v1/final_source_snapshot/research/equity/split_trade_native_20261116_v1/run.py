"""Serial bounded offline native fixture processes; not strategy optimization."""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime

from bootstrap import HERE, ROOT, save, sha


def require_freeze():
    for path, digest in json.loads((HERE / "implementation_freeze.json").read_text()).items():
        if sha(path) != digest:
            raise RuntimeError("Frozen implementation drift: " + path)


def main():
    target = HERE / "execution_receipt.json"
    if target.exists():
        raise RuntimeError("Never overwrite or restart a prior native fixture attempt")
    protocol = json.loads((HERE / "protocol.json").read_text())
    receipts = []
    for market in protocol["fixtures"]["markets"]:
        for case in protocol["fixtures"]["cases"]:
            require_freeze()
            assert shutil.disk_usage(ROOT).free > protocol["resource_policy"]["minimum_free_bytes"]
            name = market + "_" + case
            command = [
                sys.executable,
                "-B",
                str(HERE / "native_probe.py"),
                "--market",
                market,
                "--attempt",
                name,
                "--split-case",
                case,
            ]
            with (HERE / (name + ".log")).open("xb") as log:
                child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                row = {
                    "case": name,
                    "pid": child.pid,
                    "command": command,
                    "started_at": datetime.now(UTC).isoformat(),
                }
                receipts.append(row)
                save(target, receipts)
                try:
                    child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    row["timeout"] = True
                row.update(returncode=child.returncode, ended_at=datetime.now(UTC).isoformat())
                save(target, receipts)
                if child.returncode:
                    raise RuntimeError("Preserved failed native attempt: " + name)
            print(name, "complete", flush=True)
    require_freeze()


if __name__ == "__main__":
    main()
