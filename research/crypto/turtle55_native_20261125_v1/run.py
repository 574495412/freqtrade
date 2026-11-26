"""Feed a frozen source adaptation into the existing native account runner."""

import argparse
import fcntl
import logging
import shutil
import threading
import time
import traceback
from pathlib import Path

from research.crypto.donchian60_tail_native_20261125_v1 import run as native


HERE = Path(__file__).resolve().parent
NAME = "Turtle55Close"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", required=True, type=int, choices=(500, 1000))
    parser.add_argument("--cost", required=True, type=int, choices=(8, 15))
    args = parser.parse_args()
    plan, preflight, engine = native.verify_study()
    for path, digest in native.read(HERE / "freeze.json")["files"].items():
        native.checked({"path": path, "sha256": digest})

    # In this dedicated process, select the new strategy source and class for the
    # frozen runner. Its wallet, exchange, clock and account auditing stay intact.
    native.HERE, native.NAME = HERE, NAME
    output = native.ROOT / "user_data/crypto_quant" / HERE.name / f"turtle55_{args.capital}_cost{args.cost}"
    with Path(plan["shared_financial_lock"]).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(native.ROOT).free < plan["minimum_free_disk_bytes"]:
            raise ValueError("Frozen disk floor unmet")
        output.mkdir(parents=True, exist_ok=False)
        done = threading.Event()
        monitor = threading.Thread(
            target=native.monitor_resources, args=(output, plan, done, time.monotonic()), daemon=True,
        )
        monitor.start()
        try:
            native.backtest(plan, preflight, engine, args.capital, args.cost, output)
        except BaseException:
            native.save(output / "failure.json", {"traceback": traceback.format_exc(), "orders_enabled": False})
            raise
        finally:
            done.set()
            monitor.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
