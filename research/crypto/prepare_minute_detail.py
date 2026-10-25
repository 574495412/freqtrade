"""Checksummed one-minute execution data, exact real clocks and five-minute source parity."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType
from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.prepare_nfi import archive, complete


def prepare(plan_path, output):  # noqa: C901 - source checksum/calendar/parity admission
    plan = read(plan_path)
    for source in plan["sources"]:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Frozen data preparation source changed")
    if output.exists():
        raise ValueError("Preserve existing detail collection")
    base = Path(plan["base_dataset"])
    if sha(base / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Base candle receipts changed")
    parent = read(base / "receipts.json")
    output.mkdir(parents=True)
    save(output / "plan.json", plan)
    save(output / "freeze.json", {"plan_sha256": sha(plan_path), "outcomes_authorized": False})
    periods = pd.period_range(
        plan["start"], pd.Timestamp(plan["end"]) - pd.Timedelta(days=1), freq="M"
    )
    jobs = [
        (symbol, "1m", str(period), output / "raw")
        for symbol in plan["symbols"]
        for period in periods
    ]
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = [executor.submit(archive, *job) for job in jobs]
        for task in as_completed(tasks):
            try:
                records.append(task.result())
            except Exception as error:
                failures.append(str(error))
            if (len(records) + len(failures)) % 12 == 0:
                save(
                    output / "download_checkpoint.json",
                    {"completed": records, "failures": failures, "registered_archives": len(jobs)},
                )
                print(
                    {"archives": len(records), "registered": len(jobs), "failures": len(failures)},
                    flush=True,
                )
    save(output / "archive_sources.json", records)
    if failures:
        save(output / "download_failures.json", failures)
        raise RuntimeError("Source collection incomplete;do not fill missing candles")
    handler = FeatherDataHandler(output / "candles")
    coverage, hashes = [], {}
    for symbol in plan["symbols"]:
        selected = sorted([r for r in records if r["symbol"] == symbol], key=lambda r: r["period"])
        frame = complete(
            pd.concat([read_archive(Path(r["path"])) for r in selected]),
            plan["start"],
            plan["end"],
            "min",
        )
        name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        if sha(base / name) != parent[name]:
            raise ValueError("Frozen five-minute comparator changed")
        native = pd.read_feather(base / name).set_index("date")
        native = native.loc[
            (native.index >= pd.Timestamp(plan["start"], tz="UTC"))
            & (native.index < pd.Timestamp(plan["end"], tz="UTC"))
        ]
        folded = (
            frame.set_index("date")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        )
        if not native.index.equals(folded.index):
            raise ValueError("Minute aggregation calendar differs")
        errors = {}
        for field in ["open", "high", "low", "close", "volume"]:
            errors[field] = float((native[field] - folded[field]).abs().max())
            tolerance = plan["volume_absolute_tolerance"] if field == "volume" else 1e-12
            relative = plan["volume_relative_tolerance"] if field == "volume" else 1e-12
            if not np.allclose(native[field], folded[field], atol=tolerance, rtol=relative):
                save(
                    output / "source_mismatch.json",
                    {"symbol": symbol, "field": field, "maximum_errors": errors},
                )
                raise ValueError("Official1m/previously-frozen5m value mismatch")
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        handler.ohlcv_store(pair, "1m", frame, CandleType.FUTURES)
        target = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-1m-futures.feather"
        hashes[target] = sha(output / target)
        row = {
            "symbol": symbol,
            "minute_rows": len(frame),
            "five_minute_parity_rows": len(native),
            "maximum_aggregation_errors": errors,
        }
        coverage.append(row)
        save(
            output / "normalization_checkpoint.json", {"coverage": coverage, "file_hashes": hashes}
        )
        print(row, flush=True)
    save(
        output / "receipt.json",
        {
            "completed_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "raw_archives": len(records),
            "coverage": coverage,
            "file_hashes": hashes,
            "outcomes_computed": False,
            "trade_execution_proved": False,
            "production_eligible": False,
            "orders_enabled": False,
        },
    )
    print(
        {"minute_rows": sum(x["minute_rows"] for x in coverage), "outcomes_computed": False},
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan, args.output)
