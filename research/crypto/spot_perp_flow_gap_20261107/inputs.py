"""Capture final Binance daily klines and exact public funding events."""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107.catalog import fetch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/spot_perp_flow_gap_20261107_v001"
BARS = ROOT / "user_data/crypto_quant/breadth_diffusion_20261107_v001/data"
DAY_MS = 86_400_000
SPOT = "https://api.binance.com/api/v3/klines"
PERP = "https://fapi.binance.com/fapi/v1/klines"
FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"


def millis(clock):
    return pd.Timestamp(clock, tz="UTC").value // 1_000_000


def fetch_page(symbol, kind, url, query, pages):
    path = OUT / "raw" / f"{symbol}-{kind}-{query['startTime']}.json"
    raw = fetch(url, query)
    path.write_bytes(raw)
    pages[path.name] = {"url": url, "query": query, "sha256": sha(path)}
    return json.loads(raw)


def daily(plan, symbol, kind, url, pages):
    first = millis(plan["data_start"])
    end = millis(plan["windows"][-1]["end"])
    rows = []
    for begin in range(first, end, 900 * DAY_MS):
        stop = min(begin + 900 * DAY_MS, end)
        rows.extend(
            fetch_page(
                symbol,
                kind,
                url,
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "startTime": begin,
                    "endTime": stop - 1,
                    "limit": 900,
                },
                pages,
            )
        )
    expected = list(range(first, end, DAY_MS))
    if [r[0] for r in rows] != expected or any(r[6] != r[0] + DAY_MS - 1 for r in rows):
        raise ValueError(f"Incomplete daily final-candle clock for {symbol}/{kind}")
    frame = pd.DataFrame(
        [(float(r[4]), float(r[7]), float(r[10])) for r in rows],
        columns=["close", "quote", "buy_quote"],
        index=pd.to_datetime(expected, unit="ms", utc=True).rename("date"),
    )
    if (
        not np.isfinite(frame.to_numpy()).all()
        or frame[["close", "quote"]].le(0).any().any()
        or frame.buy_quote.lt(0).any()
        or frame.buy_quote.gt(frame.quote).any()
    ):
        raise ValueError(f"Invalid daily price/volume for {symbol}/{kind}")
    return frame


def funding(plan, symbol, pages):
    begin = millis(plan["windows"][0]["start"])
    end = millis(plan["windows"][-1]["end"])
    cursor = begin
    rows = []
    while cursor <= end:
        batch = fetch_page(
            symbol,
            "funding",
            FUNDING,
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end,
                "limit": 1000,
            },
            pages,
        )
        if not batch:
            break
        times = [r["fundingTime"] for r in batch]
        if times != sorted(set(times)) or not cursor <= times[0] <= times[-1] <= end:
            raise ValueError(f"Invalid funding pagination for {symbol}")
        rows.extend(batch)
        cursor = times[-1] + 1
        if len(batch) < 1000:
            break
    if any(r["symbol"] != symbol for r in rows):
        raise ValueError(f"Foreign funding row in {symbol}")
    times = pd.DatetimeIndex(pd.to_datetime([r["fundingTime"] for r in rows], unit="ms", utc=True))
    expected = pd.date_range(
        plan["windows"][0]["start"],
        plan["windows"][-1]["end"],
        freq="8h",
        tz="UTC",
        inclusive="left",
    )
    actual = times.floor("h").unique()
    if not expected.isin(actual).all():
        raise ValueError(
            f"Missing regular funding cycle in {symbol}: {list(expected.difference(actual)[:8])}"
        )
    frame = pd.DataFrame(
        [(float(r["markPrice"]), float(r["fundingRate"])) for r in rows],
        columns=["mark", "funding_rate"],
        index=times.rename("date"),
    )
    if any(
        not math.isfinite(r.mark) or r.mark <= 0 or not math.isfinite(r.funding_rate)
        for r in frame.itertuples()
    ):
        raise ValueError(f"Invalid funding price/rate for {symbol}")
    return frame


def capture(plan):
    (OUT / "raw").mkdir(parents=True)

    def one(symbol):
        pages = {}
        data = {
            "spot": daily(plan, symbol, "spot", SPOT, pages),
            "perp": daily(plan, symbol, "perp", PERP, pages),
            "funding": funding(plan, symbol, pages),
        }
        bars = pd.read_parquet(BARS / f"{symbol}_5m.parquet")
        aggregated = bars.close.resample("D").last().reindex(data["perp"].index)
        if not np.allclose(aggregated, data["perp"].close, rtol=1e-10, atol=1e-12):
            raise ValueError(f"Public daily closes differ from sealed 5m closes for {symbol}")
        files = {}
        for kind, frame in data.items():
            path = OUT / f"{symbol}-{kind}.parquet"
            frame.to_parquet(path)
            files[kind] = {"file": path.name, "sha256": sha(path), "rows": len(frame)}
        return symbol, {"pages": pages, "frames": files}

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipt = dict(pool.map(one, plan["symbols"]))
    save(OUT / "admission.json", receipt)


def panel(plan):
    admission = read(OUT / "admission.json")
    out = {}
    for symbol in plan["symbols"]:
        item = admission[symbol]
        for name, row in item["pages"].items():
            if sha(OUT / "raw" / name) != row["sha256"]:
                raise ValueError("Raw admission receipt differs")
        data = {}
        for kind, row in item["frames"].items():
            path = OUT / row["file"]
            if sha(path) != row["sha256"]:
                raise ValueError("Normalized admission receipt differs")
            data[kind] = pd.read_parquet(path)
        data["bars"] = pd.read_parquet(BARS / f"{symbol}_5m.parquet")
        out[symbol] = data
    return out
