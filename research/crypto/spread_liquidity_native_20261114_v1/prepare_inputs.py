"""Checksummed public quote-volume/funding inputs; no factor/account selection."""

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/spread_liquidity_native_20261114_v1"
SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "LTC", "DOGE"]


def capture(task):
    symbol, kind, month = task
    name = f"{symbol}USDT-{kind}-{month}.zip"
    path = OUT / "raw" / name
    folder = "klines/" + symbol + "USDT/1d" if kind == "1d" else "fundingRate/" + symbol + "USDT"
    url = f"https://data.binance.vision/data/futures/um/monthly/{folder}/{name}"
    if not path.exists():
        response = requests.get(url, timeout=30)
        checksum = requests.get(url + ".CHECKSUM", timeout=30)
        response.raise_for_status()
        checksum.raise_for_status()
        path.write_bytes(response.content)
        path.with_suffix(".CHECKSUM").write_bytes(checksum.content)
    if sha(path) != path.with_suffix(".CHECKSUM").read_text().split()[0]:
        raise ValueError("Official archive SHA mismatch")
    with ZipFile(path) as archive:
        raw = archive.read(archive.namelist()[0])
    frame = pd.read_csv(io.BytesIO(raw))
    return task, frame, {"url": url, "path": str(path), "sha256": sha(path), "checksum_sha256": sha(path.with_suffix(".CHECKSUM")), "retrieved_at": datetime.now(UTC).isoformat()}


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)
    tasks = [(symbol, kind, month.strftime("%Y-%m")) for symbol in SYMBOLS for kind in ["1d", "fundingRate"] for month in pd.date_range("2024-10-01" if kind == "1d" else "2025-01-01", "2026-08-01", freq="MS")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(capture, tasks))
    save(OUT / "public_receipts.json", [r[2] for r in fetched])
    quotes = []
    checks = []
    candles = OUT / "candles/futures"
    candles.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        daily = pd.concat([r[1] for r in fetched if r[0][0] == symbol and r[0][1] == "1d"], ignore_index=True)
        daily["date"] = pd.to_datetime(daily.open_time, unit="ms", utc=True)
        if daily.date.duplicated().any() or daily.quote_volume.le(0).any():
            raise ValueError("Invalid raw daily quote volume")
        for suffix in ["1d-futures", "1h-futures", "5m-futures", "1h-mark", "1h-funding_rate"]:
            source = ROOT / f"user_data/crypto_quant/nfi40_data_v002/candles/futures/{symbol}_USDT_USDT-{suffix}.feather"
            target = candles / source.name
            if not target.exists():
                os.link(source, target)
            elif sha(source) != sha(target):
                raise ValueError("Frozen candle drift")
        native = pd.read_feather(candles / f"{symbol}_USDT_USDT-1d-futures.feather").set_index("date")
        for row in daily.itertuples():
            for field in ["open", "high", "low", "close", "volume"]:
                if abs(float(native.loc[row.date, field]) - getattr(row, field)) > 1e-7:
                    raise ValueError(f"Official/native daily {field} conflict {symbol} {row.date}")
        daily["pair"] = f"{symbol}/USDT:USDT"
        quotes.append(daily[["date", "pair", "quote_volume"]])
        funds = pd.concat([r[1] for r in fetched if r[0][0] == symbol and r[0][1] == "fundingRate"], ignore_index=True)
        funds["date"] = pd.to_datetime(funds.calc_time, unit="ms", utc=True).dt.floor("h")
        expected = pd.date_range("2025-01-01T00:00Z", "2026-09-01T00:00Z", freq="8h", inclusive="left")
        if not pd.DatetimeIndex(funds.date).as_unit("ns").equals(expected.as_unit("ns")) or funds.funding_interval_hours.ne(8).any():
            raise ValueError("Full funding calendar not admitted")
        native_fund = pd.read_feather(candles / f"{symbol}_USDT_USDT-1h-funding_rate.feather").set_index("date").funding_rate.reindex(expected)
        if not np.allclose(native_fund, funds.last_funding_rate, rtol=0, atol=1e-12):
            raise ValueError("Native/raw funding rates conflict")
        checks.append({"symbol": symbol, "actual_quote_volume_days": len(daily), "funding_events": len(funds), "native_daily_ohlcv_match": True})
    pd.concat(quotes, ignore_index=True).to_parquet(OUT / "quote_volumes.parquet", index=False)
    save(OUT / "data_receipts.json", {str(p.relative_to(OUT / "candles")): sha(p) for p in sorted(candles.glob("*.feather"))})
    save(HERE / "input_admission.json", {"passed": True, "records": checks, "raw_archives": len(fetched), "data_receipts": {"path": str(OUT / "data_receipts.json"), "sha256": sha(OUT / "data_receipts.json")}, "quote_input": {"path": str(OUT / "quote_volumes.parquet"), "sha256": sha(OUT / "quote_volumes.parquet")}, "full_private_settlement_certified": False})
    print(json.dumps({"input_admitted": True, "archives": len(fetched), "symbols": len(SYMBOLS)}))


if __name__ == "__main__":
    main()
