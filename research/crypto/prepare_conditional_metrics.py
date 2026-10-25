"""Recover checksummed daily futures metrics without filling source gaps."""

import argparse
import hashlib
import io
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from freqtrade.research.data import read, save, sha


FIELDS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
LOCAL = threading.local()


def parse_metrics(data, symbol, day):
    with ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError("Expected one official metrics CSV")
        frame = pd.read_csv(io.BytesIO(archive.read(names[0])), dtype=str)
    if not {"create_time", "symbol", *FIELDS}.issubset(frame.columns):
        raise ValueError("Unexpected metrics schema")
    if not frame.symbol.eq(symbol).all():
        raise ValueError("Metrics symbol mismatch")
    dates = pd.to_datetime(frame.create_time, utc=True)
    start = pd.Timestamp(day, tz="UTC")
    if dates.duplicated().any():
        raise ValueError("Duplicate metrics clock")
    if not (dates.ge(start) & dates.lt(start + pd.Timedelta(days=1))).all():
        raise ValueError("Metrics row outside requested day")
    if not (dates.dt.as_unit("ns").astype("int64") % pd.Timedelta(minutes=5).value == 0).all():
        raise ValueError("Metrics row outside five-minute grid")
    result = pd.DataFrame({"raw_time": dates})
    for field in FIELDS:
        values = pd.to_numeric(frame[field], errors="coerce")
        finite = np.isfinite(values)
        result[field] = values.where(finite)
        result[field + "_value_status"] = np.where(
            finite,
            "KNOWN_RAW_VALUE",
            np.where(frame[field].isna(), "MISSING_RAW_VALUE", "INVALID_NUMERIC_VALUE"),
        )
    return result.sort_values("raw_time", kind="stable").reset_index(drop=True)


def download(symbol, day, root, base_url):
    if not hasattr(LOCAL, "session"):
        LOCAL.session = requests.Session()
    filename = f"{symbol}-metrics-{day}.zip"
    url = f"{base_url}/{symbol}/{filename}"
    path = root / "raw" / symbol / filename
    checksum = path.with_suffix(".zip.CHECKSUM")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"symbol": symbol, "day": day, "url": url, "path": str(path)}
    last_error = None
    for _attempt in range(3):
        try:
            if not checksum.exists():
                response = LOCAL.session.get(url + ".CHECKSUM", timeout=20)
                if response.status_code == 404:
                    return dict(record, status="MISSING_ARCHIVE_CHECKSUM", http_status=404)
                response.raise_for_status()
                checksum.write_bytes(response.content)
            expected = checksum.read_text().split()[0]
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("Invalid official SHA256")
            if not path.exists():
                response = LOCAL.session.get(url, timeout=30)
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != expected:
                    raise ValueError("Metrics checksum mismatch")
                temporary = path.with_suffix(".partial")
                temporary.write_bytes(response.content)
                temporary.replace(path)
            if sha(path) != expected:
                raise ValueError("Cached metrics checksum changed")
            return dict(record, status="VERIFIED", sha256=expected, checksum_sha256=sha(checksum))
        except (OSError, requests.RequestException, ValueError) as exc:
            last_error = str(exc)
    return dict(record, status="FAILED", error=last_error)


def prepare(plan_path, output, reuse_download=None):  # noqa: C901
    plan = read(plan_path)
    if output.exists():
        raise ValueError("Use a new metrics directory")
    output.mkdir(parents=True)
    save(output / "plan.json", plan)
    (output / "preparer_snapshot.py").write_bytes(Path(__file__).read_bytes())
    spec = plan["metrics_download"]
    days = pd.date_range(spec["start"], spec["end"], inclusive="left", freq="D")
    jobs = [(symbol, date.strftime("%Y-%m-%d")) for symbol in plan["symbols"] for date in days]
    records = []
    if reuse_download is not None:
        if read(reuse_download / "plan.json") != plan:
            raise ValueError("Cannot reuse a different download protocol")
        sources = read(reuse_download / "sources.json")
        if {(r["symbol"], r["day"]) for r in sources} != set(jobs):
            raise ValueError("Reused archive population differs")
        for record in sources:
            if record["status"] != "VERIFIED":
                records.append(record)
                continue
            original = Path(record["path"])
            checksum = original.with_suffix(".zip.CHECKSUM")
            if sha(original) != record["sha256"] or sha(checksum) != record["checksum_sha256"]:
                raise ValueError("Reused official source bytes changed")
            target = output / "raw" / record["symbol"] / original.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            shutil.copyfile(checksum, target.with_suffix(".zip.CHECKSUM"))
            records.append(dict(record, path=str(target), source_original_path=str(original)))
    else:
        with ThreadPoolExecutor(max_workers=spec["max_workers"]) as pool:
            pending = [
                pool.submit(download, symbol, day, output, spec["base_url"]) for symbol, day in jobs
            ]
            for future in as_completed(pending):
                records.append(future.result())
                if len(records) % 120 == 0:
                    save(
                        output / "download_progress.json",
                        {
                            "completed": len(records),
                            "scheduled": len(jobs),
                            "verified": sum(r["status"] == "VERIFIED" for r in records),
                        },
                    )
                    print(read(output / "download_progress.json"), flush=True)
    records.sort(key=lambda r: (r["symbol"], r["day"]))
    save(output / "sources.json", records)
    calendar = pd.date_range(spec["start"], spec["end"], inclusive="left", freq="5min", tz="UTC")
    coverage = []
    for symbol in plan["symbols"]:
        selected = [r for r in records if r["symbol"] == symbol]
        frames = [
            parse_metrics(Path(r["path"]).read_bytes(), symbol, r["day"])
            for r in selected
            if r["status"] == "VERIFIED"
        ]
        if not frames:
            raise ValueError("No usable metrics for registered symbol")
        frame = pd.concat(frames).set_index("raw_time").reindex(calendar)
        for field in FIELDS:
            frame[field + "_value_status"] = frame[field + "_value_status"].fillna("MISSING_ROW")
        frame.rename_axis("raw_time").reset_index().to_parquet(
            output / f"{symbol}.parquet", index=False
        )
        coverage.append(
            {
                "symbol": symbol,
                "calendar_rows": len(frame),
                "verified_days": sum(r["status"] == "VERIFIED" for r in selected),
                "positive_oi_rows": int(frame.sum_open_interest.gt(0).sum()),
                "zero_oi_rows": int(frame.sum_open_interest.eq(0).sum()),
                "unknown_oi_rows": int(frame.sum_open_interest.isna().sum()),
            }
        )
    save(output / "coverage.json", coverage)
    save(
        output / "result.json",
        {
            "protocol_sha256": sha(plan_path),
            "preparer_sha256": sha(Path(__file__)),
            "scheduled_archives": len(jobs),
            "verified_archives": sum(r["status"] == "VERIFIED" for r in records),
            "source_failures": [r for r in records if r["status"] != "VERIFIED"],
            "source_rows_sorted_without_value_changes": True,
            "reused_download": str(reuse_download) if reuse_download else None,
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print({"completed": str(output), "coverage": coverage}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-download", type=Path)
    args = parser.parse_args()
    prepare(args.plan.absolute(), args.output.absolute(), args.reuse_download)
