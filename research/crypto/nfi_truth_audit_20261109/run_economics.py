"""Serial native financial jobs. Account/order results are never synthesized here."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.nfi_blacklist_rules_20261109.inputs import read, save, sha


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001/accounts"


def main(registration_path=None):
    registration = read(registration_path or HERE / "economic-registration.json")
    for key in ("runner", "capital", "observer", "protocol"):
        item = registration[key]
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Economic source changed after registration")
    OUT.mkdir(exist_ok=True)
    completed = []
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    for case in registration["plans"]:
        if sha(case["plan"]["path"]) != case["plan"]["sha256"]:
            raise ValueError("Frozen economic plan changed")
        key = case.get("output_key", f"{case['name']}_{case['cost']}bps")
        folder = OUT / key
        if not (folder / "result.json").exists():
            if folder.exists():
                raise FileExistsError("Preserve partial/failed account")
            if shutil.disk_usage(OUT).free < 12 * 1024**3:
                raise OSError("Insufficient native cache admission space")
            with (OUT / (key + ".log")).open("x") as log:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        registration["runner"]["path"],
                        "--plan",
                        case["plan"]["path"],
                        "--candidate",
                        case["name"],
                        "--cost-bps",
                        str(case["cost"]),
                        "--output",
                        str(folder),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            save(
                folder / "completion.json",
                {
                    "returncode": result.returncode,
                    "completed_at_utc": datetime.now(UTC).isoformat(),
                    "orders_enabled": False,
                },
            )
            if result.returncode:
                raise RuntimeError("Native account failed: " + key)
        if (
            read(folder / "completion.json")["returncode"] != 0
            or not read(folder / "fill_cash_audit.json")["passed"]
        ):
            raise ValueError("Native account not completed and cash reconciled")
        cache = folder / "lossless_cache"
        if cache.exists():
            size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
            shutil.rmtree(cache)
            save(
                folder / "generated-cache-cleanup.json",
                {
                    "bytes_removed": size,
                    "scope": (
                        "Successful generated cache only; native results, inputs "
                        "and ledgers retained"
                    ),
                },
            )
        completed.append(key)
        save(
            OUT
            / (f"{registration_path.stem}-progress.json" if registration_path else "progress.json"),
            {"completed": completed, "total": len(registration["plans"])},
        )
        r = read(folder / "result.json")["periods"][0]
        print(
            json.dumps(
                {
                    "completed": key,
                    "net_return": r["net_return"],
                    "drawdown": r["five_minute_mark_drawdown"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--registration", type=Path)
    main(parser.parse_args().registration)
