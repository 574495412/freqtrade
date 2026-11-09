# ruff: noqa: C901
"""Serial native account invocations after the schema-only technical repair."""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / "user_data/crypto_quant/nfi80_historical_universe_20261109_v001/accounts-v002"


def read(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    Path(p).write_text(json.dumps(value, indent=2, default=str) + "\n")


def main():
    import hashlib

    protocol = read(HERE / "plans.json")
    for item in [
        *protocol["sources"].values(),
        *protocol["new_sources"].values(),
        protocol["protocol"],
        protocol["runner"],
        protocol["old_parity"],
    ]:
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Frozen source/protocol changed: " + item["path"])
    if (
        not read(HERE / "preflight-result.json")["passed"]
        or not read(protocol["old_parity"]["path"])["passed"]
    ):
        raise ValueError("Native schema or exact account parity missing")
    OUT.mkdir(exist_ok=True)
    completed = []
    for cost in protocol["costs"]:
        for name, item in protocol["plans"].items():
            if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Frozen plan changed")
            key = f"{name}_{cost}bps"
            folder = OUT / key
            if not (folder / "result.json").exists():
                if folder.exists():
                    raise FileExistsError("Retain unsuccessful native attempt")
                if shutil.disk_usage(OUT).free < 4 * 1024**3:
                    raise OSError("Native replay disk guard")
                with (OUT / (key + ".log")).open("x") as log:
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            protocol["runner"]["path"],
                            "--plan",
                            item["path"],
                            "--candidate",
                            name,
                            "--cost-bps",
                            str(cost),
                            "--output",
                            str(folder),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                save(
                    folder / "completion.json",
                    {
                        "returncode": child.returncode,
                        "finished_at_utc": datetime.now(UTC).isoformat(),
                        "orders_enabled": False,
                    },
                )
                if child.returncode:
                    raise RuntimeError("Native account failed " + key)
            if (
                read(folder / "completion.json")["returncode"] != 0
                or not read(folder / "fill_cash_audit.json")["passed"]
            ):
                raise ValueError("Native exit/cash audit did not pass")
            result = read(folder / "result.json")
            completed.append(key)
            save(OUT / "progress.json", {"completed": completed, "total": 4})
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                shutil.rmtree(cache)
                save(
                    folder / "generated-cache-cleanup.json",
                    {
                        "bytes_removed": size,
                        "scope": "Successful generated cache only; financial evidence retained",
                    },
                )
            print(
                {
                    "completed": key,
                    "periods": result["periods"],
                    "seconds": result["elapsed_seconds"],
                },
                flush=True,
            )


if __name__ == "__main__":
    main()
