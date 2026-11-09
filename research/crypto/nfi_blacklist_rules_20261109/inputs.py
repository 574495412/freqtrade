"""Prepare causal public inputs; reuse verified inputs without mutating prior studies."""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rapidjson

from research.crypto.nfi80_historical_universe_20261109 import prepare as prior
from research.crypto.nfi80_historical_universe_20261109.prepare import (
    PLAN,
    ROOT,
    read,
    ref,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_blacklist_rules_20261109_v001"
OLD = prior.OUT
BASE = prior.base.HERE


def candidates():
    author = rapidjson.loads((BASE / "author-universe.json").read_text(), parse_mode=3)
    markets = read(PLAN["original_full_market_snapshot"]["path"])
    return [
        p
        for p in author["exchange"]["pair_whitelist"]
        if markets["markets"].get(p, {}).get("active") and markets["leverage_tiers"].get(p)
    ]


def structural_rules():
    rules = rapidjson.loads((BASE / "author-blacklist.json").read_text(), parse_mode=3)["exchange"][
        "pair_blacklist"
    ]
    selected = [
        r
        for r in rules
        if r.startswith((".*(_PREMIUM", ".{3,}", "(ARS|", "(AEUR|", ".*STOCK", "(AAPL|", "(CL|"))
    ]
    if len(selected) != 7:
        raise ValueError("Structural rule extraction does not uniquely match the frozen source")
    return selected


def rebind():
    prior.OUT = OUT
    prior.HERE = HERE
    prior.OLD = OLD
    prior.PLAN = {**PLAN, "pairs": read(OLD / "schedule.json")["union"]}


def daily():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Frozen policy inputs are immutable")
    OUT.mkdir(exist_ok=True)
    (OUT / "daily").mkdir(exist_ok=True)
    for p in (OLD / "daily").glob("*.json"):
        destination = OUT / "daily" / p.name
        if not destination.exists():
            os.link(p, destination)
    save(
        HERE / "eligibility.json",
        {
            "candidates": candidates(),
            "structural_blacklist": structural_rules(),
            "author_universe": ref(BASE / "author-universe.json"),
            "old_blacklist": ref(BASE / "author-blacklist.json"),
            "frozen_markets": PLAN["original_full_market_snapshot"],
            "protocol": ref(HERE / "PROTOCOL.md"),
        },
    )
    rebind()
    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(prior.daily, p): p for p in candidates()}
        for job in as_completed(jobs):
            pair = jobs[job]
            try:
                _, frame = job.result()
                destination = OUT / "daily" / (pair.split("/")[0] + ".parquet")
                frame.to_parquet(destination, index=False)
            except Exception as error:
                failures.append({"pair": pair, "type": type(error).__name__, "error": str(error)})
                save(OUT / "daily-failures.json", failures)
                print({"failed": pair, "type": type(error).__name__}, flush=True)
            print(
                {
                    "stage": "daily",
                    "finished": len(jobs) - sum(not f.done() for f in jobs),
                    "total": len(jobs),
                },
                flush=True,
            )
    if failures:
        raise RuntimeError("Retained daily intake failures; no eligibility substitutions")


def execution_pair(pair):
    import pandas as pd

    markets = read(PLAN["original_full_market_snapshot"]["path"])
    onboard = pd.to_datetime(
        int(markets["markets"][pair]["info"]["onboardDate"]), unit="ms", utc=True
    )
    if onboard > prior.base.WARM:
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "research.crypto.nfi_blacklist_rules_20261109.listed_contract",
                pair,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            raise RuntimeError("Listed-contract intake failed: " + process.stderr[-1500:])
        symbol = pair.split(":")[0].replace("/", "")
        return read(OUT / "raw" / symbol / "admitted.json")
    return prior.execution_pair(pair)


def execution():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Frozen execution inputs are immutable")
    rebind()
    (OUT / "candles/futures").mkdir(parents=True, exist_ok=True)
    (OUT / "candles/selectors").mkdir(exist_ok=True)
    save(
        HERE / "input-protocol.json",
        {
            "start": PLAN["start"],
            "end": PLAN["end"],
            "comparison_end": PLAN["comparison_end"],
            "source_datadir": read(BASE / "protocol.json")["source_datadir"],
            "pairs": candidates(),
            "research_drawdown_limit": 0.30,
        },
    )
    records = {}
    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(execution_pair, p): p for p in candidates()}
        for job in as_completed(jobs):
            pair = jobs[job]
            try:
                records[pair] = job.result()
            except Exception as error:
                failures.append({"pair": pair, "type": type(error).__name__, "error": str(error)})
                save(OUT / "execution-failures.json", failures)
                print({"failed": pair, "type": type(error).__name__}, flush=True)
            save(
                OUT / "intake-progress.json",
                {"complete": len(records), "total": len(jobs), "failures": len(failures)},
            )
            print(
                {
                    "stage": "execution",
                    "complete": len(records),
                    "total": len(jobs),
                    "failed": len(failures),
                },
                flush=True,
            )
    if failures:
        raise RuntimeError("Retained execution intake failures; no eligibility substitutions")
    receipts = {n: h for row in records.values() for n, h in row["candles"].items()}
    for pair in candidates():
        source = OUT / "daily" / (pair.split("/")[0] + ".parquet")
        destination = OUT / "candles/selectors" / source.name
        if not destination.exists():
            os.link(source, destination)
        elif sha(source) != sha(destination):
            raise ValueError("Historical selector data changed")
        receipts["selectors/" + source.name] = sha(destination)
    save(OUT / "data-receipts.json", receipts)
    save(OUT / "admission.json", records)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["daily", "execution"])
    globals()[p.parse_args().stage]()
