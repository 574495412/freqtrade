# ruff: noqa: C901
"""Serial actual native policy accounts, including an exact known-account control."""

import shutil
import subprocess
import sys
from datetime import UTC, datetime

from research.crypto.nfi_blacklist_rules_20261109.inputs import HERE, OUT, read, save, sha
from research.crypto.nfi_blacklist_rules_20261109.parity import validate


def main():
    protocol = read(HERE / "plans.json")
    for item in [*protocol["sources"].values(), protocol["runner"], protocol["protocol"]]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Frozen source/protocol differs")
    if not read(HERE / "preflight-result.json")["passed"]:
        raise ValueError("Actual native preflight missing")
    account_root = OUT / "accounts"
    account_root.mkdir(exist_ok=True)
    completed = []
    total = 2 * (len(protocol["plans"]) - 1) + 1
    for cost in protocol["costs"]:
        for name, item in protocol["plans"].items():
            if name == "parity" and cost != 15:
                continue
            if sha(item["path"]) != item["sha256"]:
                raise ValueError("Registered plan differs")
            key = f"{name}_{cost}bps"
            folder = account_root / key
            if not (folder / "result.json").exists():
                if folder.exists():
                    raise FileExistsError("Preserve failed/partial native account")
                if shutil.disk_usage(OUT).free < 4 * 1024**3:
                    raise OSError("Native disk admission failed")
                with (account_root / (key + ".log")).open("x") as log:
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
                    raise RuntimeError("Native policy failed " + key)
            if (
                read(folder / "completion.json")["returncode"] != 0
                or not read(folder / "fill_cash_audit.json")["passed"]
            ):
                raise ValueError("Native account incomplete or cash not reconciled")
            if name == "parity":
                validate(folder)
            result = read(folder / "result.json")
            completed.append(key)
            save(OUT / "progress.json", {"completed": completed, "total": total})
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                shutil.rmtree(cache)
                save(
                    folder / "generated-cache-cleanup.json",
                    {
                        "bytes_removed": size,
                        "scope": "Only successful generated cache; all financial evidence retained",
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
