"""Append the preregistered holdout using public Binance market data only."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked

from .run import HERE, OUTPUT, read, receipt, save


START = pd.Timestamp("2026-11-05T00:00:00Z")
END = pd.Timestamp("2026-11-25T00:00:00Z")
BASE = "https://fapi.binance.com/fapi/v1/"
PARENT = OUTPUT.parent / "nfi40_post_source_data_20261105_v002"
OUT = OUTPUT / "holdout_inputs"
FIELDS = ["open", "high", "low", "close", "volume"]


def response(symbol, endpoint, start, end, limit, interval=None):
    url = BASE + endpoint
    params = {"symbol": symbol, "startTime": start.value // 1_000_000,
              "endTime": end.value // 1_000_000 - 1, "limit": limit}
    if interval is not None:
        params["interval"] = interval
    request = requests.get(url, params=params, timeout=30)
    request.raise_for_status()
    body = request.json()
    if not isinstance(body, list) or not body or len(body) > limit:
        raise ValueError(f"Invalid response for {symbol}/{endpoint}/{start}")
    path = OUT / "raw" / f"{symbol}-{endpoint}-{start:%Y-%m-%d}.json"
    save(path, {"url": url, "params": params, "body": body,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat()})
    return body, receipt(path)


def candles(rows, start, end, interval):
    columns = pd.DataFrame(rows)
    actual = pd.DatetimeIndex(pd.to_datetime(columns[0], unit="ms", utc=True)).as_unit("ns")
    expected = pd.date_range(start, end, freq=interval, inclusive="left").as_unit("ns")
    if not actual.equals(expected):
        raise ValueError(f"Gap or duplicate in {interval} clock from {start}")
    frame = columns.iloc[:, 1:6].astype(float)
    frame.columns = FIELDS
    if (not np.isfinite(frame.to_numpy()).all()
            or (frame[FIELDS[:4]] <= 0).any().any() or (frame.volume < 0).any()
            or (frame.high < frame[["open", "low", "close"]].max(axis=1)).any()
            or (frame.low > frame[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError("Invalid Binance OHLCV")
    return frame.set_axis(actual.rename("date")).reset_index()


def fetch_pair(pair, parent_receipts):
    base = pair.split("/")[0]
    symbol = base + "USDT"
    parts, raw = [], []
    for at in pd.date_range(START, END, freq="5D", inclusive="left"):
        stop = min(at + pd.Timedelta(days=5), END)
        body, source = response(symbol, "klines", at, stop, 1440, "5m")
        parts.append(candles(body, at, stop, "5min"))
        raw.append(source)
    five = pd.concat(parts, ignore_index=True)
    marks, source = response(symbol, "markPriceKlines", START, END, 480, "1h")
    raw.append(source)
    funding, source = response(symbol, "fundingRate", START, END, 600)
    raw.append(source)
    if any(row["symbol"] != symbol for row in funding) or len(funding) >= 600:
        raise ValueError(f"Unexpected or truncated funding for {symbol}")
    funding_frame = pd.DataFrame({
        "date": pd.to_datetime([row["fundingTime"] for row in funding], unit="ms", utc=True).floor("h"),
        "funding_rate": [float(row["fundingRate"]) for row in funding],
    })
    if (funding_frame.date.duplicated().any() or not funding_frame.date.is_monotonic_increasing
            or not funding_frame.date.between(START, END, inclusive="left").all()
            or not np.isfinite(funding_frame.funding_rate).all()):
        raise ValueError(f"Bad funding clock or rates for {symbol}")
    new_frames = {"5m-futures": five, "1h-mark": candles(marks, START, END, "1h"),
                  "1h-funding_rate": funding_frame}
    for label, interval in (("15m", "15min"), ("1h", "1h"), ("4h", "4h"), ("1d", "1D")):
        new_frames[f"{label}-futures"] = five.resample(interval, on="date").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).reset_index()
    files = {}
    for label, addition in new_frames.items():
        relative = f"futures/{base}_USDT_USDT-{label}.feather"
        old = checked({"path": PARENT / "candles" / relative, "sha256": parent_receipts[relative]})
        archived = pd.read_feather(old)
        if list(archived.columns) != list(addition.columns) or archived.date.iloc[-1] >= START:
            raise ValueError(f"Unexpected warm-up schema or clock for {relative}")
        addition["date"] = addition.date.dt.as_unit("us")
        combined = pd.concat([archived, addition], ignore_index=True)
        if label == "1h-funding_rate":
            around = combined.date.loc[combined.date.between(START - pd.Timedelta(hours=9), END)]
            if len(around) < 2 or around.iloc[0] >= START:
                raise ValueError(f"Missing funding boundary for {symbol}")
            worst = max(around.diff().max(), END - around.iloc[-1])
            if worst > pd.Timedelta(hours=9):
                raise ValueError(f"Unexplained funding gap for {symbol}")
        else:
            step = addition.date.iloc[1] - addition.date.iloc[0]
            if (archived.date.iloc[-1] != START - step or combined.date.duplicated().any()
                    or not combined.date.is_monotonic_increasing):
                raise ValueError(f"Invalid appended candle calendar for {symbol}")
        path = OUT / "candles" / relative
        combined.to_feather(path)
        files[relative] = receipt(path)["sha256"]
    return pair, files, raw


def main():
    plan = read(HERE / "plan.json")
    checked(plan["protocol"])
    parent_path = PARENT / "data-receipts.json"
    parent_receipts = read(parent_path)
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "raw").mkdir()
    (OUT / "candles/futures").mkdir(parents=True)
    completed, errors = {}, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        workers = {pool.submit(fetch_pair, pair, parent_receipts): pair for pair in plan["pairs"]}
        for future in as_completed(workers):
            pair = workers[future]
            try:
                pair, files, raw = future.result()
                completed[pair] = {"files": files, "raw": raw}
            except (OSError, ValueError, requests.RequestException) as error:
                errors[pair] = repr(error)
            print(f"{len(completed) + len(errors)}/{len(workers)} {pair} errors={len(errors)}",
                  flush=True)
    save(OUT / "coverage.json", {"start": str(START), "end_exclusive": str(END),
         "parent_receipts": receipt(parent_path), "completed": completed, "errors": errors})
    if errors:
        raise SystemExit("Incomplete holdout data; native account not admitted")
    save(OUT / "data-receipts.json", {
        name: digest for row in completed.values() for name, digest in row["files"].items()
    })


if __name__ == "__main__":
    main()
