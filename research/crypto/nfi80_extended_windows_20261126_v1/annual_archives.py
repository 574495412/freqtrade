"""Collect checksummed USD-M monthly archives for the frozen 2025 native replay."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import local
import time

import pandas as pd
import requests

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import ROOT, checked, read, receipt, save

from .inputs import HERE, OUT


DEST = OUT / "annual_2025"
SOURCE = "https://data.binance.vision/data/futures/um/monthly/"
HTTPS = local()
MIN_FREE = 3 * 1024 ** 3
ARCHIVE_CHECK = re.compile(rb"([0-9a-f]{64})\s+(\S+)")
LOCAL_MARKERS = {
    "klines": ("klines",),
    "markPriceKlines": ("markPriceKlines", "marks"),
    "fundingRate": ("fundingRate", "funding"),
}


def archive_checksum(payload, filename):
    match = ARCHIVE_CHECK.fullmatch(payload.strip())
    if match is None or match[2].decode("ascii") != filename:
        raise ValueError(f"Checksum does not identify {filename}")
    return match[1].decode("ascii")


def public_blob(url):
    if not hasattr(HTTPS, "session"):
        HTTPS.session = requests.Session()
    for attempt in range(4):
        try:
            response = HTTPS.session.get(url, timeout=(8, 30))
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            code = error.response.status_code if error.response is not None else None
            if attempt == 3 or (code is not None and code < 500 and code != 429):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("Unreachable download state")


def local_archives():
    """Index exact vendor checksum basenames, without inspecting unrelated payloads."""
    store = ROOT / "user_data/crypto_quant"
    result = subprocess.run([
        "rg", "--files", "--hidden", "--no-ignore", "-g", "*.zip.CHECKSUM", str(store),
    ], check=True, capture_output=True, text=True)
    inventory = defaultdict(list)
    for name in result.stdout.splitlines():
        check = Path(name)
        archive = check.with_suffix("")
        if DEST in archive.parents or not archive.is_file():
            continue
        try:
            fields = check.read_bytes().split()
            if len(fields) != 2:
                continue
            filename = fields[1].decode("ascii")
            if not re.fullmatch(r"[A-Z0-9]+-(?:5m|4h|1d|fundingRate)-20\d\d-\d\d\.zip", filename):
                continue
            by_name = [family for family in LOCAL_MARKERS if archive.name.startswith(family + "-")]
            by_parent = [family for family, markers in LOCAL_MARKERS.items()
                         if any(label in archive.parts for label in markers)]
            family = (by_name or by_parent or
                      (["fundingRate"] if "-fundingRate-" in filename else []))
            if len(family) != 1:
                continue
            identity = family[0], filename
            inventory[identity].append((archive, check, archive_checksum(check.read_bytes(), filename)))
        except (OSError, ValueError, UnicodeError):
            continue
    return inventory


def store_raw(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def admit_archive(symbol, family, period, interval, known):
    suffix = interval if interval else family
    filename = f"{symbol}-{suffix}-{period}.zip"
    address = f"{family}/{symbol}/" + (f"{interval}/" if interval else "") + filename
    url = SOURCE + address
    path = DEST / "raw" / address
    sha_file = path.with_suffix(".zip.CHECKSUM")
    label = {"family": family, "interval": interval, "month": period, "url": url}
    if path.is_file() and sha_file.is_file():
        digest = archive_checksum(sha_file.read_bytes(), filename)
        return {**label, "state": "cached", "archive": receipt(checked({"path": path, "sha256": digest})),
                "checksum": receipt(sha_file)}
    copies = sorted(known.get((family, filename), ()), key=lambda row: str(row[0]))
    if len({row[2] for row in copies}) == 1:
        for original, original_checksum, digest in copies:
            if receipt(original)["sha256"] == digest:
                return {**label, "state": "reused", "archive": receipt(original),
                        "checksum": receipt(original_checksum)}
    published = public_blob(url + ".CHECKSUM")
    if published is None:
        return {**label, "state": "missing_checksum"}
    digest = archive_checksum(published, filename)
    blob = public_blob(url)
    if blob is None:
        return {**label, "state": "missing_archive"}
    if hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError(f"Archive differs from the official checksum: {url}")
    if shutil.disk_usage(DEST).free < MIN_FREE:
        raise OSError("Insufficient disk space for further archive writes")
    store_raw(path, blob)
    store_raw(sha_file, published)
    return {**label, "state": "downloaded", "archive": receipt(path), "checksum": receipt(sha_file)}


def registered_sources():
    policy_path = HERE / "annual_protocol.json"
    policy = read(policy_path)
    start, stop = map(pd.Timestamp, policy["window"])
    availability = read(HERE / "2025_availability.json")
    activation = read(checked(availability["schedule"]))
    assert availability["rankable_days"] == len(activation["days"]) == (stop - start).days
    parents = read(checked(availability["parent"]))
    rules = read(checked(parents["original_full_market_snapshot"]))
    cases = {}
    for pair in activation["union"]:
        market = rules["markets"][pair]
        actual = pd.Timestamp(int(market["info"]["onboardDate"]), unit="ms", tz="UTC")
        snapshot = checked(activation["daily_sources"][pair])
        prior = pd.Timestamp(int(read(snapshot)[0][0]), unit="ms", tz="UTC")
        # A more recent onboarding date conflicts with older exchange candles.
        # Preserve both clocks and request the earlier information.
        listing = min(actual, prior)
        jobs = []
        for family, interval, tag, end in (
            ("klines", "5m", "5m", stop),
            ("markPriceKlines", "5m", "mark_and_funding", stop),
            ("fundingRate", None, "mark_and_funding", stop),
            ("klines", "4h", "4h", pd.Timestamp(policy["warmup"]["5m"])),
            ("klines", "1d", "1d", stop),
        ):
            first = max(listing, pd.Timestamp(policy["warmup"][tag]))
            last = end - pd.Timedelta(seconds=1)
            if first <= last:
                periods = pd.period_range(first.tz_localize(None), last.tz_localize(None), freq="M")
                jobs.extend((family, str(period), interval) for period in periods)
        cases[pair] = {"symbol": market["id"], "onboard": str(actual), "daily_first": str(prior),
                       "sources": jobs}
    subject = {
        "protocol": receipt(policy_path), "schedule": availability["schedule"],
        "parent": availability["parent"], "market_snapshot": parents["original_full_market_snapshot"],
        "cases": cases, "orders_enabled": False,
    }
    return subject


def collect_pair(item, inventory):
    pair, case = item
    symbol = case["symbol"]
    target = DEST / "pairs" / f"{symbol}.json"
    current = {}
    if target.exists():
        old = read(target)
        assert old["pair"] == pair
        current = {(row["family"], row["month"], row["interval"]): row for row in old["archives"]
                   if "archive" in row}
    admitted = []
    for job in case["sources"]:
        if job in current:
            row = current[job]
            checked(row["archive"])
            checked(row["checksum"])
        else:
            try:
                row = admit_archive(symbol, *job, inventory)
            except (requests.RequestException, OSError, ValueError) as error:
                family, period, interval = job
                row = {"family": family, "month": period, "interval": interval,
                       "state": "failed", "error": str(error)}
        admitted.append(row)
    save(target, {"pair": pair, "sources_expected": len(case["sources"]), "archives": admitted})
    return receipt(target), sum("archive" in source for source in admitted), len(admitted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "pairs").mkdir(exist_ok=True)
    plan = DEST / "raw_plan.json"
    registered = registered_sources()
    # JSON roundtrip provides a stable representation for tuple-based jobs.
    if plan.exists():
        loaded = read(plan)
        for name, case in loaded["cases"].items():
            case["sources"] = [tuple(row) for row in case["sources"]]
        assert loaded == registered
    else:
        save(plan, registered)
    cases = registered["cases"]
    total = sum(len(case["sources"]) for case in cases.values())
    print("frozen", len(cases), total, flush=True)
    if args.plan_only:
        return
    inventory = local_archives()
    print("known exact archive names", len(inventory), flush=True)
    rows = {}
    completed, requested = 0, 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        pending = {pool.submit(collect_pair, item, inventory): item[0] for item in cases.items()}
        for future in as_completed(pending):
            pair = pending[future]
            rows[pair], good, seen = future.result()
            completed += good
            requested += seen
            if len(rows) % 10 == 0 or len(rows) == len(cases):
                print("archive intake", len(rows), len(cases), completed, requested, flush=True)
                save(DEST / "intake_progress.json", {
                    "raw_plan": receipt(plan), "contract_count": len(rows),
                    "archive_count": completed, "attempted_count": requested, "total_expected": total,
                    "pair_receipts": rows, "normalized_inputs_admitted": False, "orders_enabled": False,
                })


if __name__ == "__main__":
    main()
