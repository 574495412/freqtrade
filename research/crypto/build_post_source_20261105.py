"""Build a hash-verified warmup plus post-source native dataset from frozen archives."""
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from freqtrade.research.data import read_archive  # noqa: E402

RESEARCH = ROOT / "research/crypto/strategy_comparison_wide_20261105"
OLD = ROOT / "user_data/crypto_quant/nfi40_data_v002"
RAW = ROOT / "user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001"
OUT = ROOT / "user_data/crypto_quant/nfi40_post_source_data_20261105_v002"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path):
    return {"path": str(path), "sha256": sha(path)}


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def complete(frame, start, end, freq):
    frame = frame.sort_values("date").reset_index(drop=True)
    expected = pd.date_range(start, end, freq=freq, inclusive="left")
    if not pd.DatetimeIndex(frame.date).equals(expected):
        raise ValueError(f"Incomplete or duplicated archive calendar: {freq} {start}..{end}")
    numeric = frame.select_dtypes(include="number")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Nonfinite archive values")
    return frame


def main():
    completion = json.loads((RAW / "download-completion.json").read_text())
    if sha(RAW / "sources.json") != completion["sources_sha256"]:
        raise ValueError("Raw source manifest changed")
    if sha(RAW / "registered_protocol.json") != completion["registered_protocol_sha256"]:
        raise ValueError("Registered post-source protocol changed")
    protocol = json.loads((RAW / "registered_protocol.json").read_text())
    original_receipts = json.loads((OLD / "receipts.json").read_text())
    sources = json.loads((RAW / "sources.json").read_text())
    supplement_path = RAW / "supplement_api_oct/sources.json"
    if json.loads((RAW / "supplement_api_oct/failures.json").read_text()):
        raise ValueError("Official API supplements are incomplete")
    supplements = json.loads(supplement_path.read_text())
    for source in supplements:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Daily supplement changed: " + source["path"])
    for source in sources:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Downloaded archive changed: " + source["path"])
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "registered_protocol.json").write_bytes((RAW / "registered_protocol.json").read_bytes())
    receipts, coverage = {}, {}
    start = pd.Timestamp("2026-09-01T00:00:00Z")
    end = pd.Timestamp(protocol["end"])
    candles = OUT / "candles/futures"
    candles.mkdir(parents=True)
    fields = ["date", "open", "high", "low", "close", "volume"]
    by_symbol = {}
    for source in sources:
        by_symbol.setdefault(source["symbol"], []).append(source)
    marks = {}
    for number, pair in enumerate(protocol["pairs"], 1):
        sym = pair.split("/")[0] + "USDT"
        prefix = pair.split("/")[0] + "_USDT_USDT-"
        entries = by_symbol[sym]
        def archive_frames(kind, timeframe=None):
            selected = [r for r in entries if r["kind"] == kind and
                        (timeframe is None or r.get("timeframe") == timeframe)]
            if len(selected) != 6:
                raise ValueError(f"Missing verified {kind}/{timeframe} archives for {sym}")
            initial = pd.concat([read_archive(Path(r["path"])) for r in selected], ignore_index=True)
            additions = [r for r in supplements if r["symbol"] == sym and r["kind"] == kind and
                         (timeframe is None or r.get("timeframe") == timeframe)]
            expected_count = 2 if kind == "mark" else 1
            if len(additions) != expected_count:
                raise ValueError(f"Missing official daily supplements for {sym}/{kind}/{timeframe}")
            api_frames = []
            for item in additions:
                response = json.loads(Path(item["path"]).read_text())
                api = pd.DataFrame(response["body"])
                frame = pd.DataFrame({"date": pd.to_datetime(api[0], unit="ms", utc=True)})
                for index, name in enumerate(("open", "high", "low", "close", "volume"), 1):
                    frame[name] = pd.to_numeric(api[index], errors="raise")
                if ((frame[["open", "high", "low", "close"]] <= 0).any().any()
                    or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
                    or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()
                    or (frame.volume < 0).any()):
                    raise ValueError("Invalid public API candle: " + item["path"])
                api_frames.append(frame)
            extra = pd.concat(api_frames, ignore_index=True)
            if not pd.DatetimeIndex(initial.date).intersection(pd.DatetimeIndex(extra.date)).empty:
                raise ValueError("Unexpected overlap in monthly and daily supplements")
            return pd.concat([initial, extra], ignore_index=True)
        five = complete(archive_frames("futures", "5m")[fields], start, end, "5min")
        day = complete(archive_frames("futures", "1d")[fields], start, end, "1D")
        mark = complete(archive_frames("mark")[fields], start, end, "1h")
        future = {"5m-futures": five, "1d-futures": day, "1h-mark": mark}
        for label, frequency in (("15m-futures", "15min"), ("1h-futures", "1h"), ("4h-futures", "4h")):
            resampled = (five.set_index("date").resample(frequency, origin="epoch")
                         .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
                         .reset_index())
            future[label] = complete(resampled, start, end, frequency)
        funding_records = [r for r in entries if r["kind"] == "funding"]
        api_records = [r for r in entries if r["kind"] == "funding_api"]
        if len(funding_records) != 2 or len(api_records) != 1:
            raise ValueError("Missing funding sources for " + sym)
        parts = []
        for source in funding_records:
            with ZipFile(source["path"]) as archive:
                filename, = archive.namelist()
                raw = pd.read_csv(io.BytesIO(archive.read(filename)))
            parts.append(pd.DataFrame({"date": pd.to_datetime(raw.calc_time, unit="ms", utc=True).dt.floor("h"),
                                       "funding_rate": pd.to_numeric(raw.last_funding_rate, errors="raise")}))
        api = json.loads(Path(api_records[0]["path"]).read_text())["body"]
        parts.append(pd.DataFrame({"date": pd.to_datetime([r["fundingTime"] for r in api], unit="ms", utc=True).floor("h"),
                                   "funding_rate": [float(r["fundingRate"]) for r in api]}))
        funding = pd.concat(parts, ignore_index=True).sort_values("date").reset_index(drop=True)
        if funding.date.duplicated().any() or funding.date.min() != start or funding.date.max() >= end or not np.isfinite(funding.funding_rate).all():
            raise ValueError("Invalid public funding event clock or rates for " + sym)
        future["1h-funding_rate"] = funding
        for label, addition in future.items():
            filename = prefix + label + ".feather"
            old = OLD / "candles/futures" / filename
            if sha(old) != original_receipts["candles/futures/" + filename]:
                raise ValueError("Original warmup changed: " + filename)
            previous = pd.read_feather(old)
            if list(previous.columns) != list(addition.columns):
                raise ValueError("Native schema changed: " + filename)
            combined = pd.concat([previous, addition], ignore_index=True)
            if combined.date.duplicated().any() or not combined.date.is_monotonic_increasing:
                raise ValueError("Invalid historical / new-data boundary: " + filename)
            path = candles / filename
            combined.reset_index(drop=True).to_feather(path)
            receipts["futures/" + filename] = sha(path)
            if label == "1h-mark":
                marks[pair] = record(path)
        coverage[sym] = {"5m_rows": len(five), "daily_rows": len(day), "mark_rows": len(mark),
                         "funding_events": len(funding), "funding_first": str(funding.date.iloc[0]),
                         "funding_last": str(funding.date.iloc[-1])}
        print(f"Built {number}/{len(protocol['pairs'])}: {sym}", flush=True)
    save(OUT / "data-receipts.json", receipts)
    save(OUT / "coverage.json", coverage)
    if len(receipts) != 280 or len(marks) != 40:
        raise ValueError("Incomplete 40-pair dataset")
    parent = json.loads((RESEARCH / "plan.json").read_text())
    parent.update({"schema": "cz.post-source-historical-account.v1",
                   "registered_at": datetime.now(timezone.utc).isoformat(),
                   "registration_note": protocol["selection_timing"],
                   "post_source_protocol": record(RAW / "registered_protocol.json"),
                   "start": protocol["start"], "end": protocol["end"],
                   "comparison_end": protocol["comparison_end"],
                   "datadir": str(OUT / "candles"), "data_receipts": record(OUT / "data-receipts.json"),
                   "marks": marks, "candidates": [parent["candidates"][0]],
                   "total_cost_bps_per_side": [8],
                   "periods": [{"name": "post-source-period", "start": protocol["start"],
                                "end": protocol["comparison_end"]}],
                   "known_limitations": [protocol["selection_timing"], protocol["account_contract"],
                                          "13-day window has limited opportunity and cannot establish long-run profitability",
                                          "40-pair pool has historical survivorship limits; current exchange metadata may differ from historical rules",
                                          "Public prices and funding do not prove customer fills, funding settlement or on-exchange protection"]})
    plan = RESEARCH / "plan-post-source.json"
    with plan.open("x") as stream:
        json.dump(parent, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    save(OUT / "completion.json", {"registered_protocol_sha256": sha(OUT / "registered_protocol.json"),
                                    "api_supplement_manifest_sha256": sha(supplement_path),
                                    "files": len(receipts), "plan": str(plan), "plan_sha256": sha(plan),
                                    "orders_enabled": False, "production_eligible": False})
    print(json.dumps({"files": len(receipts), "plan": str(plan), "plan_sha256": sha(plan)}))


if __name__ == "__main__":
    main()
