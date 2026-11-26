"""Run the first frozen weekly rank wallet using Freqtrade's native runner."""

import fcntl
import logging
import shutil
import threading
import time
import traceback
from pathlib import Path

from research.crypto.donchian60_tail_native_20261125_v1 import run as native


HERE = Path(__file__).resolve().parent
NAME = "WeeklyHighRank"
CASE = "weeklyhigh_500_cost15"


def main():
    plan, preflight, engine = native.verify_study()
    for path, digest in native.read(HERE / "financial_freeze.json")["files"].items():
        native.checked({"path": path, "sha256": digest})
    native.HERE, native.NAME = HERE, NAME
    output = native.ROOT / "user_data/crypto_quant" / HERE.name / CASE
    with Path(plan["shared_financial_lock"]).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(native.ROOT).free < plan["minimum_free_disk_bytes"]:
            raise ValueError("Frozen disk floor unmet")
        output.mkdir(exist_ok=False)
        done = threading.Event()
        monitor = threading.Thread(
            target=native.monitor_resources, args=(output, plan, done, time.monotonic()), daemon=True,
        )
        monitor.start()
        try:
            native.backtest(plan, preflight, engine, 500, 15, output)
        except BaseException:
            native.save(output / "failure.json", {"traceback": traceback.format_exc(), "orders_enabled": False})
            raise
        finally:
            done.set()
            monitor.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
