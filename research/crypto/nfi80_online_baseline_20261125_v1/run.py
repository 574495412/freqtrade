"""Run the registered candidate in a fresh offline process."""

import argparse
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from shutil import disk_usage
import subprocess
import sys

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import (
    LOCK, ROOT, checked, read, save,
)

from .register import HERE


def main(cost, study):
    plan_path = study / "plan.json"
    plan = read(plan_path)
    candidate, = plan["candidates"]
    for key in ("predecessor_plan", "protocol_receipt", "online_alignment"):
        checked(plan[key])
    for record in plan.get("mechanism_evidence", []):
        checked(record)
    if cost == 8 and not read(study / "comparison_15bp.json")["advance"]:
        raise ValueError("15bp account did not meet the frozen advance rule")
    output = ROOT / "user_data/crypto_quant" / study.name / f"cost_{cost}bp"
    if output.exists():
        raise FileExistsError(output)
    if disk_usage(ROOT).free < 4 * 1024**3:
        raise RuntimeError("Insufficient space for native replay")
    with LOCK.open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        result = subprocess.run([
            sys.executable, "-B", "-m",
            "research.crypto.nfi80_historical_universe_20261109.v003.native_runner",
            "--plan", str(plan_path), "--candidate", candidate["name"], "--cost-bps", str(cost),
            "--output", str(output),
        ], cwd=ROOT)
        save(study / f"execution_{cost}bp.json", {
            "returncode": result.returncode,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": str(output), "orders_enabled": False,
        })
        result.check_returncode()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=int, choices=(15, 8), default=15)
    parser.add_argument("--study", type=Path, default=HERE)
    args = parser.parse_args()
    main(args.cost, args.study.resolve(strict=True))
