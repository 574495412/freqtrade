"""Bounded replay of unchanged incumbents; old risk screens do not stop evaluation."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import psutil


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def stats(folder):
    path = next(folder.glob("backtest-result-*.zip"))
    with zipfile.ZipFile(path) as archive:
        name = next(
            n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        )
        return next(iter(json.loads(archive.read(name))["strategy"].values()))


def run(plan_path, output):
    plan = json.loads(plan_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    write(output / "registered_plan.json", plan)
    for name, digest in plan["core_source_hashes"].items():
        if hashlib.sha256((Path("freqtrade") / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Original native core changed: " + name)
    for name, digest in plan["supporting_source_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ValueError("Original runner/plan changed: " + name)
    for job in plan["jobs"]:
        directory = output / job["id"]
        started = time.monotonic()
        maximum = 0
        print(
            json.dumps(
                {
                    "job": job["id"],
                    "candidate": job["candidate"],
                    "phase": job["phase"],
                    "state": "start",
                }
            ),
            flush=True,
        )
        with (output / (job["id"] + ".log")).open("x") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    job["module"],
                    "--plan",
                    job["plan"],
                    "--output",
                    str(directory),
                    "--candidate",
                    job["candidate"],
                    "--phase",
                    job["phase"],
                    "--cost",
                    "3",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                try:
                    rss = psutil.Process(process.pid).memory_info().rss
                except psutil.NoSuchProcess:
                    rss = 0
                maximum = max(maximum, rss)
                elapsed = time.monotonic() - started
                write(
                    output / "progress.json",
                    {"job": job["id"], "pid": process.pid, "elapsed": elapsed, "max_rss": maximum},
                )
                if rss > plan["max_worker_rss_bytes"] or elapsed > plan["max_seconds_each"]:
                    process.kill()
                    process.wait()
                    write(
                        output / "resource_guard.json",
                        {"job": job["id"], "rss": rss, "elapsed": elapsed},
                    )
                    raise SystemExit(73)
                time.sleep(1)
            if process.returncode:
                raise SystemExit(process.returncode)
        if job.get("parity_with"):
            original = stats(Path(job["parity_with"]))
            current = stats(directory)
            # Exact JSON-valued trade/order arrays; derived floats have1e-8 tolerance.
            parity = (
                original["trades"] == current["trades"]
                and abs(original["profit_total_abs"] - current["profit_total_abs"]) < 1e-8
                and original["pairlist"] == current["pairlist"]
            )
            write(
                output / "original_parity.json",
                {
                    "passed": parity,
                    "trades": len(current["trades"]),
                    "net_pnl": current["profit_total_abs"],
                },
            )
            if not parity:
                raise SystemExit("Original replay parity failed; preserve outputs and stop")
        write(
            directory / "review_receipt.json",
            {
                "elapsed_seconds": time.monotonic() - started,
                "max_rss_bytes": maximum,
                "signals_or_parameters_changed": False,
                "original_core_files_verified": len(plan["core_source_hashes"]),
                "orders_enabled": False,
            },
        )
        print(
            json.dumps(
                {
                    "job": job["id"],
                    "state": "completed",
                    "result": json.loads((directory / "result.json").read_text())["net_return"],
                }
            ),
            flush=True,
        )
    write(
        output / "completion.json",
        {
            "bounded_unchanged_replays_completed": True,
            "jobs": len(plan["jobs"]),
            "new_factors": 0,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.plan, a.output)
