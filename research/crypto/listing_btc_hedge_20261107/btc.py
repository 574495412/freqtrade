"""Public BTC futures prices, quote turnover, marks and actual settlement clocks."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107.catalog import OUT as A05_OUT
from research.crypto.listing_reversal_20261107.catalog import fetch, now
from research.crypto.listing_reversal_20261107.intake import archive


HERE = Path(__file__).resolve().parent
OUT = A05_OUT.parent / "listing_btc_hedge_20261107_v001"
REGISTRATION = HERE / "btc_registration.json"
SYMBOL = "BTCUSDT"
START = pd.Timestamp("2024-01-01", tz="UTC")
END = pd.Timestamp("2026-09-06", tz="UTC")
API = "https://fapi.binance.com/fapi/v1/fundingRate"


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("BTC tape already registered")
    save(
        REGISTRATION,
        {
            "registered_utc": now(),
            "code_sha256": sha(Path(__file__)),
            "symbol": SYMBOL,
            "start": START.isoformat(),
            "end": END.isoformat(),
            "price_sources": ["Binance monthly 5m archive", "Binance daily 5m gap archive"],
            "funding_source": API,
            "policy": (
                "Require exact continuous mark and trade clocks; no synthetic prices. "
                "Funding keeps real millisecond times, rate and settlement mark. Verify all 8h "
                "scheduled events, accepting up to 60s publisher jitter without moving cash times."
            ),
        },
    )


def candles(family, grid):
    directory = OUT / family
    directory.mkdir()
    receipts = []
    months = [x for x in sorted(set(grid.strftime("%Y-%m"))) if x != "2026-09"]
    parts = [archive(SYMBOL, family, month, directory, receipts) for month in months]
    frames = [frame for frame in parts if frame is not None]
    if not frames:
        raise ValueError(f"No monthly archives for {family}")
    data = pd.concat(frames).set_index("date")
    data.index = data.index.as_unit("us")
    for day in grid.difference(data.index).normalize().unique():
        extra = archive(SYMBOL, family, str(day.date()), directory, receipts)
        if extra is not None:
            frame = extra.set_index("date")
            frame.index = frame.index.as_unit("us")
            data = pd.concat([data, frame.loc[~frame.index.isin(data.index)]])
    if data.index.has_duplicates:
        raise ValueError(f"Duplicate market data for {family}")
    data = data.reindex(grid)
    if data.isna().any().any():
        raise ValueError(f"Incomplete market data for {family}")
    path = OUT / f"{family}.parquet"
    data.to_parquet(path)
    save(directory / "receipts.json", receipts)
    return {"parquet": sha(path), "raw_index": sha(directory / "receipts.json"), "rows": len(data)}


def funding():
    directory = OUT / "funding"
    directory.mkdir()
    start = int(START.timestamp() * 1000)
    end = int(END.timestamp() * 1000) - 1
    rows = []
    receipts = []
    for page in range(8):
        request = {"symbol": SYMBOL, "startTime": start, "endTime": end, "limit": 1000}
        raw = directory / f"page-{page:02}.json"
        raw.write_bytes(fetch(API, request))
        page_rows = read(raw)
        if not isinstance(page_rows, list) or len(page_rows) > 1000:
            raise ValueError("Unexpected public BTC funding response")
        receipts.append({"params": request, "sha256": sha(raw), "rows": len(page_rows)})
        rows.extend(page_rows)
        if len(page_rows) < 1000:
            break
        start = page_rows[-1]["fundingTime"] + 1
    else:
        raise ValueError("Funding exceeded page bound")
    if any(row["symbol"] != SYMBOL for row in rows):
        raise ValueError("Wrong funding symbol")
    events = pd.DataFrame(
        {
            "date": pd.to_datetime([row["fundingTime"] for row in rows], unit="ms", utc=True),
            "rate": [float(row["fundingRate"]) for row in rows],
            "mark": [float(row["markPrice"]) for row in rows],
        }
    ).set_index("date")
    expected = pd.date_range(START, END, freq="8h", inclusive="left")
    if (
        events.index.has_duplicates
        or not events.index.is_monotonic_increasing
        or not events.index.round("8h").as_unit("us").equals(expected.as_unit("us"))
        or (abs(events.index - events.index.round("8h")) > pd.Timedelta(seconds=60)).any()
        or (events.mark <= 0).any()
        or not np.isfinite(events.to_numpy()).all()
    ):
        raise ValueError("Funding coverage or identity differs")
    path = OUT / "funding.parquet"
    events.to_parquet(path)
    save(directory / "receipts.json", receipts)
    return {
        "parquet": sha(path),
        "raw_index": sha(directory / "receipts.json"),
        "rows": len(events),
    }


def acquire():
    plan = read(REGISTRATION)
    if plan["code_sha256"] != sha(Path(__file__)):
        raise ValueError("Frozen BTC acquisition changed")
    OUT.mkdir()
    grid = pd.date_range(START, END, freq="5min", inclusive="left").as_unit("us")
    with ThreadPoolExecutor(max_workers=3) as pool:
        prices = {key: pool.submit(candles, key, grid) for key in ("klines", "markPriceKlines")}
        fees = pool.submit(funding)
        results = {k: job.result() for k, job in prices.items()}
        results["funding"] = fees.result()
    save(OUT / "btc_receipts.json", {"registration_sha256": sha(REGISTRATION), "sources": results})
    print(results, flush=True)


if __name__ == "__main__":
    {"register": register, "acquire": acquire}[sys.argv[1]]()
