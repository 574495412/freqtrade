"""Download checksummed Binance spot archives for the frozen short-window study."""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "research/crypto/spot_baseline_20261105/protocol.json"
OUT = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001/short-raw"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(job):
    symbol, timeframe, period = job
    frequency = "monthly" if len(period) == 7 else "daily"
    name = f"{symbol}-{timeframe}-{period}.zip"
    url = f"https://data.binance.vision/data/spot/{frequency}/klines/{symbol}/{timeframe}/{name}"
    path = OUT / "archives" / symbol / timeframe / name
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(3):
        try:
            check = requests.get(url + ".CHECKSUM", timeout=25)
            check.raise_for_status()
            expected = check.text.split()[0]
            if len(expected) != 64:
                raise ValueError("Invalid public archive checksum")
            response = requests.get(url, timeout=35)
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != expected:
                raise ValueError("Public spot archive checksum mismatch")
            path.write_bytes(response.content)
            path.with_suffix(".zip.CHECKSUM").write_bytes(check.content)
            return {"symbol": symbol, "timeframe": timeframe, "period": period, "url": url,
                    "path": str(path), "sha256": expected}
        except (requests.RequestException, OSError, ValueError) as exc:
            last = exc
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                break
            time.sleep(attempt + 0.25)
    raise RuntimeError(f"{symbol}/{timeframe}/{period}: {last}")


def main(scope):
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "registered-protocol.json").write_bytes(PROTOCOL.read_bytes())
    protocol = json.loads(PROTOCOL.read_text())
    symbols = [pair.replace("/", "") for pair in protocol["spot_pairs_registered"]]
    if scope == "short":
        periods = {
            "5m": ["2026-09", "2026-10"],
            "4h": [str(p) for p in pd.period_range("2026-06", "2026-10", freq="M")],
            "1d": [str(p) for p in pd.period_range("2024-07", "2026-10", freq="M")],
        }
        for values in periods.values():
            values.extend(f"2026-11-{day:02}" for day in range(1, 5))
    else:
        # Exact earlier extension; the short dataset supplies all later bars.
        periods = {
            "5m": [str(p) for p in pd.period_range("2024-10", "2026-08", freq="M")],
            "4h": [str(p) for p in pd.period_range("2024-08", "2026-05", freq="M")],
            "1d": [str(p) for p in pd.period_range("2022-10", "2024-06", freq="M")],
        }
    jobs = [(symbol, tf, period) for symbol in symbols for tf, windows in periods.items() for period in windows]
    sources, failures = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                sources.append(future.result())
            except Exception as exc:
                failures.append({"job": futures[future], "error": str(exc)})
            done = len(sources) + len(failures)
            if done % 100 == 0 or done == len(jobs):
                (OUT / "progress.json").write_text(json.dumps({"done": done, "total": len(jobs),
                                                               "failures": len(failures)}, indent=2) + "\n")
                print(f"Spot archives {done}/{len(jobs)}, failures={len(failures)}", flush=True)
    sources.sort(key=lambda row: (row["symbol"], row["timeframe"], row["period"]))
    (OUT / "sources.json").write_text(json.dumps(sources, indent=2) + "\n")
    (OUT / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
    (OUT / "download-receipt.json").write_text(json.dumps({"jobs": len(jobs), "failed": len(failures),
                                                           "sources_sha256": sha(OUT / "sources.json"),
                                                           "protocol_sha256": sha(OUT / "registered-protocol.json"),
                                                           "economic_result_calculated": False}, indent=2) + "\n")
    if failures:
        raise SystemExit("Some spot archives are missing; investigate data eligibility before any account run")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("short", "long"), default="short")
    args = parser.parse_args()
    OUT = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001" / (args.scope + "-raw")
    main(args.scope)
