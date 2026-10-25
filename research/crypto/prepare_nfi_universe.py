"""Prepare the frozen wider NFI pool using official price and funding-interval archives."""

import argparse
import hashlib
import io
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType
from freqtrade.research.data import download_mark, read, read_archive, save, sha
from research.crypto.prepare_nfi import prepare


def funding_archive(symbol, period, folder):
    name = f"{symbol}-fundingRate-{period}.zip"
    url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{name}"
    path = folder / symbol / name
    path.parent.mkdir(parents=True, exist_ok=True)
    check = path.with_suffix(".zip.CHECKSUM")
    for attempt in range(3):
        try:
            if not check.exists():
                response = requests.get(url + ".CHECKSUM", timeout=20)
                response.raise_for_status()
                check.write_bytes(response.content)
            expected = check.read_text().split()[0]
            if not path.exists():
                response = requests.get(url, timeout=20)
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != expected:
                    raise ValueError("Funding download SHA mismatch")
                path.write_bytes(response.content)
            if sha(path) != expected:
                raise ValueError("Funding cache SHA mismatch")
            return {
                "kind": "funding",
                "symbol": symbol,
                "month": period,
                "path": str(path),
                "url": url,
                "sha256": expected,
            }
        except (requests.RequestException, OSError, ValueError):
            if attempt == 2:
                raise
    raise RuntimeError("No funding archive result")


def confirm_funding_window(raw, index, symbol, destination):
    """Require the authoritative public history to agree across an irregular clock gap."""
    left, right = raw.date.iloc[index - 1], raw.date.iloc[index]
    params = {
        "symbol": symbol,
        "startTime": int(left.value // 1_000_000),
        "endTime": int(right.value // 1_000_000) + 1000,
        "limit": 1000,
    }
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{symbol}_{params['startTime']}_{params['endTime']}.json"
    if path.exists():
        receipt = read(path)
        if receipt["params"] != params:
            raise ValueError("Funding API receipt parameters changed")
    else:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        receipt = {
            "url": url,
            "params": params,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "body": response.json(),
            "orders_enabled": False,
        }
        save(path, receipt)
    body = receipt["body"]
    if not isinstance(body, list) or not 2 <= len(body) < 1000:
        raise ValueError("Funding API did not cover both sides of the irregular window")
    if any(row["symbol"] != symbol for row in body):
        raise ValueError("Funding API returned another contract")
    times = pd.to_datetime([r["fundingTime"] for r in body], unit="ms", utc=True).floor("h")
    archived = raw.loc[raw.date.between(left, right)]
    if not times.equals(pd.DatetimeIndex(archived.date)):
        raise ValueError("Archive and authoritative funding event calendars disagree")
    rates = [Decimal(str(r["fundingRate"])) for r in body]
    if rates != [Decimal(str(r)) for r in archived.last_funding_rate]:
        raise ValueError("Archive and authoritative funding rates disagree")
    return {
        "path": str(path),
        "sha256": sha(path),
        "start": left.isoformat(),
        "end": right.isoformat(),
        "events": len(body),
    }


def funding_frame(records, start, end, *, symbol=None, reconciliation_dir=None):
    frames = []
    for record in records:
        with ZipFile(record["path"]) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise ValueError("Ambiguous funding CSV")
            frames.append(pd.read_csv(io.BytesIO(archive.read(names[0]))))
    raw = pd.concat(frames).sort_values("calc_time").reset_index(drop=True)
    event_times = pd.to_datetime(raw.calc_time, unit="ms", utc=True)
    raw["date"] = event_times.dt.floor("h")
    if (event_times - raw.date).dt.total_seconds().max() > 1:
        raise ValueError("Funding event clock exceeds the one-second hour alignment allowance")
    raw["funding_rate"] = pd.to_numeric(raw.last_funding_rate, errors="raise")
    if raw.date.duplicated().any() or not np.isfinite(raw.funding_rate).all():
        raise ValueError("Invalid funding event identities or rates")
    intervals = pd.to_numeric(raw.funding_interval_hours, errors="raise")
    if not np.isfinite(intervals).all() or intervals.le(0).any():
        raise ValueError("Invalid published funding interval")
    gaps = raw.date.diff().dt.total_seconds().iloc[1:] / 3600
    irregular = gaps.index[~gaps.eq(intervals.iloc[1:])]
    confirmations = []
    for index in irregular:
        if symbol is None or reconciliation_dir is None:
            raise ValueError("Funding event gaps do not match the published accrual intervals")
        confirmations.append(confirm_funding_window(raw, index, symbol, reconciliation_dir))
    if raw.date.iloc[0] != pd.Timestamp(start, tz="UTC") or (
        raw.date.iloc[-1] + pd.Timedelta(hours=float(intervals.iloc[-1]))
        != pd.Timestamp(end, tz="UTC")
    ):
        raise ValueError("Funding boundary not fully covered")
    raw.attrs["api_verified_irregular_windows"] = confirmations
    return raw


def seed_cache(source: Path, target: Path):
    if not source.exists():
        return
    for path in source.rglob("*"):
        if not path.is_file() or not path.name.endswith((".zip", ".CHECKSUM")):
            continue
        dest = target / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            os.link(path, dest)


def copy_prices(stage, candles):
    receipts = read(stage / "receipts.json")
    for source in (stage / "candles/futures").glob("*-futures.feather"):
        if sha(source) != receipts[str(source.relative_to(stage))]:
            raise ValueError("Kline stage changed")
        shutil.copyfile(source, candles / source.name)


def auxiliary_archive(job, output):
    kind, symbol, month = job
    if kind == "mark":
        return dict(download_mark((symbol, month, output / "mark_raw")), kind="mark")
    return funding_archive(symbol, month, output / "raw/fundingRate")


def build(plan_path, output, baseline, old_nfi):
    plan = read(plan_path)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "receipts.json").exists():
        raise ValueError("Completed dataset cannot be overwritten")
    stage = output.with_name(output.name + "_klines")
    seed_cache(old_nfi / "raw", stage / "raw")
    seed_cache(baseline / "mark_raw", output / "mark_raw")
    data_plan = dict(plan, base_start="2023-08-01", daily_start="2021-10-01")
    if not (stage / "receipts.json").exists():
        print("Preparing full price calendars", flush=True)
        prepare(data_plan, stage, baseline)
    else:
        if read(stage / "plan.json") != data_plan:
            raise ValueError("Kline stage protocol differs")
    save(output / "plan.json", plan)
    months = [str(p) for p in pd.period_range("2023-12", "2026-08", freq="M")]
    jobs = [
        (kind, symbol, month)
        for kind in ("mark", "funding")
        for symbol in plan["symbols"]
        for month in months
    ]

    records, failures = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(auxiliary_archive, job, output): job for job in jobs}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as error:
                failures.append({"job": futures[future], "error": str(error)})
            if (len(records) + len(failures)) % 80 == 0:
                print(
                    f"Funding/mark {len(records)}/{len(jobs)}, failures={len(failures)}", flush=True
                )
    save(output / "sources.json", records)
    save(output / "failures.json", failures)
    if failures:
        raise ValueError("Archive requests failed; resume only the same frozen pool")
    candles = output / "candles/futures"
    candles.mkdir(parents=True, exist_ok=True)
    copy_prices(stage, candles)
    handler = FeatherDataHandler(output / "candles")
    coverage = {}
    for symbol in plan["symbols"]:
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        funds = funding_frame(
            [r for r in records if r["kind"] == "funding" and r["symbol"] == symbol],
            "2023-12-01",
            plan["holdout_end"],
            symbol=symbol,
            reconciliation_dir=output / "funding_api",
        )
        handler.ohlcv_store(pair, "1h", funds[["date", "funding_rate"]], CandleType.FUNDING_RATE)
        funds.to_parquet(output / f"{symbol}_funding_events.parquet", index=False)
        mark = pd.concat(
            [
                read_archive(Path(r["path"]))
                for r in records
                if r["kind"] == "mark" and r["symbol"] == symbol
            ]
        )
        expected = pd.date_range(
            "2023-12-01", plan["holdout_end"], freq="h", inclusive="left", tz="UTC"
        )
        missing = expected.difference(pd.DatetimeIndex(mark.date))
        for day in sorted(set(missing.strftime("%Y-%m-%d"))):
            extra = dict(download_mark((symbol, day, output / "mark_raw")), kind="mark_supplement")
            records.append(extra)
            supplement = read_archive(Path(extra["path"]))
            mark = pd.concat([mark, supplement.loc[supplement.date.isin(missing)]])
        mark = mark.sort_values("date").reset_index(drop=True)
        if not pd.DatetimeIndex(mark.date).equals(expected):
            raise ValueError(f"Incomplete mark calendar: {symbol}")
        handler.ohlcv_store(pair, "1h", mark, CandleType.MARK)
        coverage[symbol] = {
            "funding_events": len(funds),
            "mark_hours": len(mark),
            "funding_intervals_hours": sorted(funds.funding_interval_hours.unique().tolist()),
            "api_verified_irregular_windows": funds.attrs["api_verified_irregular_windows"],
        }
    save(output / "sources.json", records)
    save(
        output / "result.json",
        {
            "coverage": coverage,
            "klines_stage_receipts_sha256": sha(stage / "receipts.json"),
            "plan_sha256": sha(output / "plan.json"),
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return read(output / "result.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--old-nfi", type=Path, required=True)
    args = parser.parse_args()
    print(
        build(
            args.plan.resolve(),
            args.output.resolve(),
            args.baseline.resolve(),
            args.old_nfi.resolve(),
        )
    )
