"""Download verified public Binance archives for a fixed NFI replication protocol."""

import argparse
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType
from freqtrade.research.data import read, read_archive, save, sha


def archive(symbol: str, timeframe: str, period: str, root: Path) -> dict:
    frequency = "monthly" if len(period) == 7 else "daily"
    filename = f"{symbol}-{timeframe}-{period}.zip"
    url = (
        f"https://data.binance.vision/data/futures/um/{frequency}/klines/"
        f"{symbol}/{timeframe}/{filename}"
    )
    path = root / symbol / timeframe / filename
    checksum = path.with_suffix(".zip.CHECKSUM")
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for _attempt in range(3):
        try:
            if not checksum.exists():
                result = requests.get(url + ".CHECKSUM", timeout=20)
                result.raise_for_status()
                checksum.write_bytes(result.content)
            expected = checksum.read_text().split()[0]
            if len(expected) != 64:
                raise ValueError("Invalid official checksum")
            if not path.exists():
                result = requests.get(url, timeout=30)
                result.raise_for_status()
                if hashlib.sha256(result.content).hexdigest() != expected:
                    raise ValueError("Downloaded archive checksum mismatch")
                temporary = path.with_suffix(".partial")
                temporary.write_bytes(result.content)
                temporary.replace(path)
            if sha(path) != expected:
                raise ValueError("Cached archive checksum mismatch")
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "period": period,
                "path": str(path),
                "url": url,
                "sha256": expected,
            }
        except (requests.RequestException, OSError, ValueError) as error:
            last_error = error
    raise RuntimeError(f"Archive failed: {url}: {last_error}")


def complete(frame: pd.DataFrame, start: str, end: str, freq: str) -> pd.DataFrame:
    expected = pd.date_range(start, end, inclusive="left", freq=freq, tz="UTC")
    result = frame.loc[frame.date.ge(expected[0]) & frame.date.lt(pd.Timestamp(end, tz="UTC"))]
    result = result.sort_values("date").reset_index(drop=True)
    if not pd.DatetimeIndex(result.date).equals(expected):
        raise ValueError(f"Missing or duplicate real candles: {freq}")
    return result


def prepare(plan: dict, output: Path, funding_dataset: Path) -> dict:  # noqa: C901
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "plan.json"
    if plan_path.exists() and read(plan_path) != plan:
        raise ValueError("Cannot resume a different protocol")
    if (output / "receipts.json").exists():
        raise ValueError("Dataset already completed; use its recorded receipts")
    save(plan_path, plan)
    records, jobs = [], []
    for timeframe, start in (("5m", plan["base_start"]), ("1d", plan["daily_start"])):
        periods = pd.period_range(
            start, pd.Timestamp(plan["holdout_end"]) - pd.Timedelta(days=1), freq="M"
        )
        jobs.extend(
            (symbol, timeframe, str(period), output / "raw")
            for symbol in plan["symbols"]
            for period in periods
        )
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(archive, *job): job for job in jobs}
        failures = []
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as error:
                failures.append(str(error))
            if (len(records) + len(failures)) % 24 == 0:
                print(f"Archives {len(records)}/{len(jobs)}, failures={len(failures)}", flush=True)
    save(output / "archive_sources.json", records)
    if failures:
        save(output / "download_failures.json", failures)
        raise RuntimeError(
            f"{len(failures)} archives unavailable; explicit same-protocol resume supported"
        )
    candles_dir = output / "candles"
    candles_dir.mkdir(exist_ok=True)
    handler = FeatherDataHandler(candles_dir)
    coverage = {}
    for symbol in plan["symbols"]:
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        coverage[symbol] = {}
        for timeframe, start, freq in (
            ("5m", plan["base_start"], "5min"),
            ("1d", plan["daily_start"], "D"),
        ):
            frame = pd.concat(
                [
                    read_archive(Path(r["path"]))
                    for r in records
                    if r["symbol"] == symbol and r["timeframe"] == timeframe
                ]
            )
            expected = pd.date_range(
                start, plan["holdout_end"], inclusive="left", freq=freq, tz="UTC"
            )
            missing = expected.difference(pd.DatetimeIndex(frame.date))
            for day in sorted(set(missing.strftime("%Y-%m-%d"))):
                record = archive(symbol, timeframe, day, output / "raw")
                records.append(record)
                supplement = read_archive(Path(record["path"]))
                frame = pd.concat([frame, supplement.loc[supplement.date.isin(missing)]])
            frame = complete(frame, start, plan["holdout_end"], freq)
            handler.ohlcv_store(pair, timeframe, frame, CandleType.FUTURES)
            coverage[symbol][timeframe] = len(frame)
            if timeframe == "5m":
                for larger, rule in (("15m", "15min"), ("1h", "h"), ("4h", "4h")):
                    aggregated = (
                        frame.set_index("date")
                        .resample(rule, origin="epoch")
                        .agg(
                            {
                                "open": "first",
                                "high": "max",
                                "low": "min",
                                "close": "last",
                                "volume": "sum",
                                "quote_volume": "sum",
                            }
                        )
                        .reset_index()
                    )
                    aggregated = complete(aggregated, start, plan["holdout_end"], rule)
                    handler.ohlcv_store(pair, larger, aggregated, CandleType.FUTURES)
                    coverage[symbol][larger] = len(aggregated)
    # Copy only checksummed mark/funding inputs; do not alter the first frozen dataset.
    parent_receipts = read(funding_dataset / "receipts.json")
    copied = []
    for name, identity in parent_receipts.items():
        if name.startswith("candles/") and name.endswith(
            ("-mark.feather", "-funding_rate.feather")
        ):
            source = funding_dataset / name
            if sha(source) != identity:
                raise ValueError("Parent funding/mark data changed")
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append({"path": name, "sha256": identity})
    if len(copied) != 12:
        raise ValueError("Expected six real funding and six mark files")
    save(output / "archive_sources.json", records)
    result = {
        "schema": "crypto-quant-nfi-data.v1",
        "coverage": coverage,
        "copied_funding_mark": copied,
        "plan_sha256": sha(plan_path),
        "parent_receipts_sha256": sha(funding_dataset / "receipts.json"),
        "aggregation": "complete 5m UTC left-closed intervals; no imputed candles",
        "orders_enabled": False,
    }
    save(output / "result.json", result)
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("research/crypto/nfi_plan.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--funding-dataset", type=Path, required=True)
    arguments = parser.parse_args()
    print(
        prepare(
            read(arguments.plan), arguments.output.resolve(), arguments.funding_dataset.resolve()
        )
    )
