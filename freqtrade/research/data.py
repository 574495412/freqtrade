"""验证已有官方档案并转换为Freqtrade格式，不复制旧研究账户。"""

import hashlib
import io
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read(path: Path):
    return json.loads(path.read_text())


def read_archive(path: Path) -> pd.DataFrame:
    """只读ZIP内CSV；明确识别官方表头和毫秒/微秒时间单位。"""
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError(f"档案不含唯一CSV: {path.name}")
        raw = pd.read_csv(io.BytesIO(archive.read(names[0])), header=None)
    if raw.iloc[0, 0] in ("open_time", "open_time_ms"):
        raw = raw.iloc[1:]
    values = raw.iloc[:, :8].apply(pd.to_numeric, errors="raise")
    unit = "us" if values.iloc[0, 0] > 1e14 else "ms"
    result = pd.DataFrame({"date": pd.to_datetime(values[0], unit=unit, utc=True)})
    for index, name in enumerate(("open", "high", "low", "close", "volume"), 1):
        result[name] = values[index].astype(float)
    result["quote_volume"] = values[7].astype(float)
    if not np.isfinite(result.iloc[:, 1:].to_numpy()).all():
        raise ValueError("行情包含非有限数值")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("行情包含非正价格")
    if (
        (result["high"] < result[["open", "close", "low"]].max(axis=1))
        | (result["low"] > result[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("OHLC关系不合法")
    if (result[["volume", "quote_volume"]] < 0).any().any():
        raise ValueError("成交量不能为负")
    return result.reset_index(drop=True)


def complete_hours(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    frame = (
        frame.loc[
            (frame["date"] >= pd.Timestamp(start, tz="UTC"))
            & (frame["date"] < pd.Timestamp(end, tz="UTC"))
        ]
        .sort_values("date")
        .reset_index(drop=True)
    )
    expected = pd.date_range(start, end, freq="h", inclusive="left", tz="UTC")
    if not pd.DatetimeIndex(frame["date"]).equals(expected):
        raise ValueError("小时行情缺失、重复或起止边界不完整，禁止静默填充")
    return frame


def download_mark(job):
    symbol, month, destination = job
    name = f"{symbol}-1h-{month}.zip"
    frequency = "monthly" if len(month) == 7 else "daily"
    url = f"https://data.binance.vision/data/futures/um/{frequency}/markPriceKlines/{symbol}/1h/{name}"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / name
    checksum_path = path.with_suffix(".zip.CHECKSUM")
    for attempt in range(3):
        try:
            if checksum_path.exists() and path.exists():
                checksum = checksum_path.read_bytes()
            else:
                with urllib.request.urlopen(url + ".CHECKSUM", timeout=20) as response:  # noqa: S310
                    checksum = response.read()
            expected = checksum.decode().split()[0]
            if not path.exists():
                with urllib.request.urlopen(url, timeout=20) as response:
                    body = response.read()
                if hashlib.sha256(body).hexdigest() != expected:
                    raise ValueError("官方mark档案SHA不一致")
                path.write_bytes(body)
            if sha(path) != expected:
                raise ValueError("本地mark档案与官方SHA不一致")
            checksum_path.write_bytes(checksum)
            return {
                "symbol": symbol,
                "month": month,
                "path": str(path),
                "url": url,
                "sha256": expected,
                "verified_at": datetime.now(UTC).isoformat(),
            }
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("下载没有完成")


def source_candles(source: Path, symbol: str, records: list[dict], receipts: list) -> pd.DataFrame:
    frames = []
    for record in records:
        if record["symbol"] != symbol:
            continue
        path = source / "archives" / symbol / "1h" / f"{symbol}-1h-{record['month']}.zip"
        if sha(path) != record["sha256"] or not record["checksum_verified"]:
            raise ValueError(f"原始行情SHA不一致: {path}")
        frames.append(read_archive(path))
        receipts.append({"path": str(path), "sha256": sha(path), "kind": "futures"})
    return pd.concat(frames)


def prepare_data(source: Path, output: Path, plan: dict, *, resume: bool = False) -> dict:
    """复用已封存原始K线和资金费；另取官方mark小时归档。"""
    if resume and (read(output / "plan.json") != plan or (output / "result.json").exists()):
        raise ValueError("只允许恢复相同协议且尚未完成的数据准备")
    output.mkdir(parents=True, exist_ok=resume)
    save(output / "plan.json", plan)
    manifest = read(source / "manifest.json")
    start, end = plan["warmup_start"], plan["holdout_end"]
    months = pd.period_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), freq="M")
    months = [str(month) for month in months]
    records = [
        record
        for record in manifest["sources"]
        if record["symbol"] in plan["symbols"] and record["month"] in months
    ]
    if len(records) != len(months) * len(plan["symbols"]):
        raise ValueError("所需完整品种/月度档案不存在")
    quotes, coverage, receipts = [], {}, []
    (output / "candles").mkdir(exist_ok=resume)
    handler = FeatherDataHandler(output / "candles")
    for symbol in plan["symbols"]:
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        candles = complete_hours(source_candles(source, symbol, records, receipts), start, end)
        handler.ohlcv_store(pair, "1h", candles, CandleType.FUTURES)
        quotes.append(candles[["date", "quote_volume"]].assign(pair=pair))
        funding = []
        expected = {record["sha256"] for record in manifest["funding_sources"][symbol]}
        found = set()
        for path in sorted((source / "funding_raw" / symbol).glob("*.json")):
            if path.name.endswith(".receipt.json"):
                continue
            identity = sha(path)
            if identity not in expected:
                raise ValueError("资金费原文未登记在来源清单")
            found.add(identity)
            funding.extend(read(path))
            receipts.append({"path": str(path), "sha256": identity, "kind": "funding"})
        if found != expected:
            raise ValueError("资金费原文缺失")
        fund = pd.DataFrame(funding)
        fund["original_date"] = pd.to_datetime(fund["fundingTime"], unit="ms", utc=True)
        fund["date"] = fund["original_date"].dt.floor("h")
        fund["funding_rate"] = pd.to_numeric(fund["fundingRate"], errors="raise")
        fund = fund.loc[
            (fund["date"] >= pd.Timestamp(start, tz="UTC"))
            & (fund["date"] < pd.Timestamp(end, tz="UTC"))
        ].sort_values("date")
        expected_times = pd.date_range(start, end, freq="8h", inclusive="left", tz="UTC")
        if not pd.DatetimeIndex(fund["date"]).equals(expected_times):
            raise ValueError(f"{symbol}资金费不符合声明的完整8小时日历，需另立适配")
        offset = (fund["original_date"] - fund["date"]).dt.total_seconds().max()
        if offset > 1 or not np.isfinite(fund["funding_rate"]).all():
            raise ValueError("资金费时间偏差超过1秒或数值无效")
        fund.assign(pair=pair).to_parquet(output / f"{symbol}_funding_events.parquet", index=False)
        handler.ohlcv_store(pair, "1h", fund[["date", "funding_rate"]], CandleType.FUNDING_RATE)
        coverage[symbol] = {
            "hours": len(candles),
            "funding_events": len(fund),
            "funding_max_clock_floor_seconds": float(offset),
        }
    jobs = [(symbol, month, output / "mark_raw") for symbol in plan["symbols"] for month in months]
    with ThreadPoolExecutor(max_workers=6) as executor:
        marks = list(executor.map(download_mark, jobs))
    save(output / "mark_sources.json", marks)
    for symbol in plan["symbols"]:
        frames = [read_archive(Path(item["path"])) for item in marks if item["symbol"] == symbol]
        combined = pd.concat(frames)
        expected_hours = pd.date_range(start, end, freq="h", inclusive="left", tz="UTC")
        missing = expected_hours.difference(pd.DatetimeIndex(combined["date"]))
        # 官方月档可能缺日，只按缺失日历补官方日档，不填造价格。
        for day in sorted(set(missing.strftime("%Y-%m-%d"))):
            supplement = download_mark((symbol, day, output / "mark_raw"))
            marks.append(supplement)
            extra = read_archive(Path(supplement["path"]))
            combined = pd.concat([combined, extra.loc[extra["date"].isin(missing)]])
        frame = complete_hours(combined, start, end)
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        handler.ohlcv_store(pair, "1h", frame, CandleType.MARK)
    save(output / "mark_sources.json", marks)
    pd.concat(quotes).to_parquet(output / "quote_volume.parquet", index=False)
    save(output / "input_sources.json", receipts)
    result = {
        "schema": "crypto-quant-data.v1",
        "coverage": coverage,
        "plan_sha256": sha(output / "plan.json"),
        "source_manifest_sha256": sha(source / "manifest.json"),
        "funding_convention": "原始实际费率，最多1秒向下归小时；采用官方小时mark开盘代理结算估值",
        "historical_market_rules": "当前规则快照，未证明历史逐时资格",
        "leader_labels_read": False,
    }
    save(output / "result.json", result)
    save(
        output / "receipts.json",
        {
            str(path.relative_to(output)): sha(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
    )
    return result
