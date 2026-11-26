"""Extend frozen native input clocks with public Binance market data."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import requests

from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_inputs import candles
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import ROOT, checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1.holdout_schedule import (
    daily_series, policy_for, ranking,
)


HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant" / HERE.name
PARENT = HERE.parent / "nfi80_online_baseline_20261125_v1"
API = "https://fapi.binance.com/fapi/v1/"
PACE = threading.Lock()
NEXT_REQUEST = 0.0


def milliseconds(value):
    return value.value // 1_000_000


def public_rows(symbol, route, start, end, limit, interval=None):
    global NEXT_REQUEST
    params = {"symbol": symbol, "startTime": milliseconds(start),
              "endTime": milliseconds(end) - 1, "limit": limit}
    if interval is not None:
        params["interval"] = interval
    name = f"{route}-{interval or 'events'}-{params['startTime']}-{params['endTime']}.json"
    path = OUT / "raw" / symbol / name
    if path.is_file():
        raw = read(path)
        assert raw["url"] == API + route and raw["params"] == params
        return raw["rows"], receipt(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        with PACE:
            time.sleep(max(0, NEXT_REQUEST - time.monotonic()))
            NEXT_REQUEST = time.monotonic() + 0.35
        try:
            response = requests.get(API + route, params=params, timeout=(8, 30))
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code != 429 and response.status_code < 500:
            break
        time.sleep(min(45, max(2, int(response.headers.get("Retry-After", "3")))))
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError(f"Invalid public response for {symbol}/{route}/{start}")
    save(path, {"url": API + route, "params": params, "rows": rows,
                "retrieved_at_utc": datetime.now(UTC).isoformat()})
    return rows, receipt(path)


def bar_rows(symbol, route, start, end, interval, step):
    parts, sources = [], []
    for first in pd.date_range(start, end, freq=step * 1000, inclusive="left"):
        stop = min(end, first + step * 1000)
        body, proof = public_rows(symbol, route, first, stop, 1000, interval)
        if len(body) != (stop - first) // step:
            raise ValueError(f"Incomplete {symbol} {interval} from {first}")
        parts.extend(body)
        sources.append(proof)
    return parts, sources


def concurrently(items, task, label):
    answers, errors = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = {pool.submit(task, pair): pair for pair in items}
        for done in as_completed(pending):
            pair = pending[done]
            try:
                answers[pair] = done.result()
            except (ValueError, KeyError, AssertionError, OSError, requests.RequestException) as error:
                errors[pair] = repr(error)
            complete = len(answers) + len(errors)
            if complete % 20 == 0 or complete == len(items):
                print(label, complete, len(items), len(errors), flush=True)
    if errors:
        save(OUT / f"{label}_missing.json", errors)
        raise RuntimeError(f"Missing {label} input; see {label}_missing.json")
    return answers


def capture_schedule(plan, start, end, markets):
    _, _, eligible, _, previous = policy_for(plan)
    stop_before = milliseconds(pd.Timestamp(plan["end"]))
    day = pd.Timedelta(days=1)

    def daily_for(pair):
        seed = previous["daily_sources"][pair]
        old = [row for row in read(checked(seed)) if int(row[6]) < stop_before]
        beginning = pd.Timestamp(int(old[-1][0]), unit="ms", tz="UTC") + day
        new, raw = bar_rows(markets[pair]["id"], "klines", beginning, end, "1d", day)
        all_rows = old + new
        opens, _, _ = daily_series(all_rows)
        assert opens[-1] == milliseconds(end - day), pair
        path = OUT / "daily" / f"{markets[pair]['id']}.json"
        save(path, all_rows)
        return all_rows, {"source": receipt(path), "prefix": seed, "raw": raw}

    frames = concurrently(eligible, daily_for, "selection")
    series = {pair: daily_series(row[0]) for pair, row in frames.items()}
    days = {d.date().isoformat(): ranking(series, milliseconds(d))
            for d in pd.date_range(start, end, freq="D", inclusive="left")}
    union = sorted({pair for row in days.values() for pair in row["pairs"]})
    schedule = {"days": days, "union": union, "candidate_count": len(eligible),
                "daily_sources": {pair: item[1] for pair, item in frames.items()}}
    save(OUT / "schedule.json", schedule)
    return schedule, {pair: item[0] for pair, item in frames.items()}


def extend_pair(pair, plan, archives, daily, start, end, market):
    sources = []
    files = {}
    stem = pair.replace("/", "_").replace(":", "_")
    symbol = market["id"]
    flux_start = start - pd.Timedelta(days=1)

    def archived(name):
        key = f"futures/{stem}-{name}.feather"
        if key not in archives:
            return None
        path = Path(plan["datadir"]) / key
        checked({"path": str(path), "sha256": archives[key]})
        return pd.read_feather(path)

    def store(name, original, extension):
        extension["date"] = extension.date.dt.as_unit("us")
        joined = extension if original is None else pd.concat([original, extension], ignore_index=True)
        assert joined.date.is_unique and joined.date.is_monotonic_increasing, (pair, name)
        key = f"futures/{stem}-{name}.feather"
        path = OUT / "candles" / key
        joined.to_feather(path)
        files[key] = receipt(path)["sha256"]
        return joined

    def extend_bar(tag, route, interval, empty_start):
        step = pd.Timedelta(interval)
        prev = archived(tag)
        first = empty_start if prev is None else prev.date.iloc[-1] + step
        rows, raw = bar_rows(symbol, route, first, end, interval, step)
        sources.extend(raw)
        extension = candles(rows, first, end, step)
        return store(tag, prev, extension)

    earliest_daily = pd.Timestamp(int(daily[0][0]), unit="ms", tz="UTC")
    for interval in ("5m", "15m", "1h", "4h"):
        step = pd.Timedelta(interval)
        warmup = max(start - step * 800, earliest_daily.ceil(step))
        extend_bar(f"{interval}-futures", "klines", interval, warmup)

    old_daily = archived("1d-futures")
    daily_start = earliest_daily if old_daily is None else old_daily.date.iloc[-1] + pd.Timedelta(days=1)
    new_daily = [row for row in daily if int(row[0]) >= milliseconds(daily_start)]
    store("1d-futures", old_daily, candles(new_daily, daily_start, end, "D"))

    mark = extend_bar("5m-mark", "markPriceKlines", "5m", flux_start)
    old_mark = archived("1h-mark")
    start_hour = flux_start if old_mark is None else old_mark.date.iloc[-1] + pd.Timedelta(hours=1)
    hour_groups = mark.loc[mark.date >= start_hour].resample("1h", on="date")
    assert hour_groups.size().eq(12).all(), pair
    hour = hour_groups.agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).reset_index()
    hourly = store("1h-mark", old_mark, hour)

    old_rates = archived("1h-funding_rate")
    rate_start = flux_start if old_rates is None else old_rates.date.iloc[-1]
    rates = []
    for a in pd.date_range(rate_start, end, freq="7D", inclusive="left"):
        body, raw = public_rows(symbol, "fundingRate", a, min(end, a + pd.Timedelta(days=7)), 200)
        assert len(body) < 200 and all(row["symbol"] == symbol for row in body)
        rates.extend(body)
        sources.append(raw)
    date = pd.DatetimeIndex(pd.to_datetime([r["fundingTime"] for r in rates], unit="ms", utc=True)).floor("h")
    new_rates = pd.DataFrame({"date": date, "funding_rate": [float(r["fundingRate"]) for r in rates]})
    if old_rates is not None:
        new_rates = new_rates.loc[new_rates.date > old_rates.date.iloc[-1]]
    assert np.isfinite(new_rates.funding_rate).all()
    all_rates = store("1h-funding_rate", old_rates, new_rates)
    check = all_rates.date.loc[all_rates.date.between(flux_start, end, inclusive="left")]
    assert check.notna().all() and len(check) > 1
    assert (check.diff().dropna() <= pd.Timedelta(hours=9)).all(), pair
    assert check.iloc[0] - flux_start <= pd.Timedelta(hours=9), pair
    assert end - check.iloc[-1] <= pd.Timedelta(hours=9), pair
    assert check.isin(hourly.date).all(), pair
    return {"files": files, "raw": sources}


def main(stage):
    protocol = HERE / "protocol.json"
    start, end = map(pd.Timestamp, read(protocol)["recent_window"])
    plan = read(PARENT / "plan.json")
    assert start > pd.Timestamp(plan["comparison_end"])
    assert start == start.normalize() < end == end.normalize() <= pd.Timestamp.now(tz="UTC").normalize()
    OUT.mkdir(parents=True, exist_ok=True)
    anchor = OUT / "frozen_protocol.json"
    if anchor.exists():
        assert read(anchor) == receipt(protocol)
    else:
        save(anchor, receipt(protocol))
    (OUT / "daily").mkdir(exist_ok=True)
    market_file = checked(plan["original_full_market_snapshot"])
    market = read(market_file)
    schedule, daily = capture_schedule(plan, start, end, market["markets"])
    if stage == "selection":
        print("Frozen daily ranking", len(schedule["days"]), len(schedule["union"]))
        return
    (OUT / "candles/futures").mkdir(parents=True, exist_ok=True)
    archive = read(checked(plan["data_receipts"]))
    union = schedule["union"]
    completed = concurrently(
        union,
        lambda pair: extend_pair(pair, plan, archive, daily[pair], start, end, market["markets"][pair]),
        "execution",
    )
    files = {key: digest for data in completed.values() for key, digest in data["files"].items()}
    assert len(files) == 8 * len(union)
    save(OUT / "data-receipts.json", files)
    save(OUT / "market-rules.json", {
        **market, "markets": {p: market["markets"][p] for p in union},
        "leverage_tiers": {p: market["leverage_tiers"][p] for p in union},
    })
    save(OUT / "coverage.json", {
        "protocol": receipt(protocol), "start": str(start), "end_exclusive": str(end),
        "schedule": receipt(OUT / "schedule.json"), "market_rules": receipt(OUT / "market-rules.json"),
        "files": receipt(OUT / "data-receipts.json"), "raw": completed, "orders_enabled": False,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("selection", "execution"))
    main(parser.parse_args().stage)
