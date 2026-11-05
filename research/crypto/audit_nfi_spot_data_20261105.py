"""Audit fixed Binance spot archives without evaluating strategy economics."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from freqtrade.research.data import read_archive  # noqa: E402

RAW = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001/short-raw"
OUT = ROOT / "research/crypto/spot_baseline_20261105/spot-data-inventory.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(scope):
    receipt = json.loads((RAW / "download-receipt.json").read_text())
    if receipt["failed"] or receipt["sources_sha256"] != sha(RAW / "sources.json"):
        raise ValueError("Incomplete or changed spot archives")
    plan = json.loads((RAW / "registered-protocol.json").read_text())
    if sha(RAW / "registered-protocol.json") != receipt["protocol_sha256"]:
        raise ValueError("Spot protocol changed")
    sources = json.loads((RAW / "sources.json").read_text())
    grouped = {}
    for source in sources:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Spot archive changed: " + source["path"])
        grouped.setdefault((source["symbol"], source["timeframe"]), []).append(source)
    if scope == "short":
        windows = {"5m": ("2026-09-01T00:00:00Z", "2026-11-05T00:00:00Z", "5min"),
                   "4h": ("2026-06-01T00:00:00Z", "2026-11-05T00:00:00Z", "4h"),
                   "1d": ("2024-07-01T00:00:00Z", "2026-11-05T00:00:00Z", "1D")}
    else:
        windows = {"5m": ("2024-10-01T00:00:00Z", "2026-09-01T00:00:00Z", "5min"),
                   "4h": ("2024-08-01T00:00:00Z", "2026-06-01T00:00:00Z", "4h"),
                   "1d": ("2022-10-01T00:00:00Z", "2024-07-01T00:00:00Z", "1D")}
    records = []
    for index, pair in enumerate(plan["spot_pairs_registered"], 1):
        symbol = pair.replace("/", "")
        for timeframe, (start, end, frequency) in windows.items():
            frames = [read_archive(Path(item["path"])) for item in grouped[(symbol, timeframe)]]
            frame = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
            actual = pd.DatetimeIndex(frame.date)
            expected = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=frequency, inclusive="left")
            missing = expected.difference(actual)
            extras = actual.difference(expected)
            records.append({"pair": pair, "symbol": symbol, "timeframe": timeframe,
                            "expected_rows": len(expected), "actual_rows": len(frame),
                            "missing_rows": len(missing), "missing_days": missing.strftime("%Y-%m-%d").unique().tolist(),
                            "duplicates": int(actual.duplicated().sum()), "outside_rows": len(extras),
                            "nonfinite_cells": int((~np.isfinite(frame.select_dtypes(include="number"))).sum().sum())})
        if index % 6 == 0:
            print(f"Spot data inspected {index}/{len(plan['spot_pairs_registered'])}", flush=True)
    result = {"schema": "cz.spot-public-data-inventory.v1", "protocol_sha256": receipt["protocol_sha256"],
              "source_manifest_sha256": receipt["sources_sha256"], "files_checked": len(sources),
              "records": records, "economic_results_observed": False, "orders_enabled": False}
    with OUT.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"records": len(records), "records_with_gaps": sum(bool(r['missing_rows']) for r in records),
                      "missing_days": sorted({day for r in records for day in r['missing_days']}),
                      "output": str(OUT), "sha256": sha(OUT)}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("short", "long"), default="short")
    args = parser.parse_args()
    RAW = ROOT / "user_data/crypto_quant/spot_baseline_inputs_20261105_v001" / (args.scope + "-raw")
    OUT = ROOT / "research/crypto/spot_baseline_20261105" / ("spot-data-inventory.json" if args.scope == "short" else "spot-long-data-inventory.json")
    main(args.scope)
