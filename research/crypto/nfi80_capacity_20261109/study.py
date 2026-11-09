# ruff: noqa: E501
"""Freeze and execute an anonymous native NFI leverage/slot experiment, never live trading."""

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import rapidjson
import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/nfi80_capacity_20261109_v001"
PROTOCOL = HERE / "protocol.json"
PARENT = ROOT / "research/crypto/strategy_comparison_wide_20261105/plan.json"
PIN = "1af55971d3a64703ec449078e09fdec257d0d3c6"
START = pd.Timestamp("2026-01-01", tz="UTC")
WARM = pd.Timestamp("2025-12-01", tz="UTC")
CASES = {"L3_S12": (3, 12), "L1_S6": (1, 6), "L3_S6": (3, 6), "L1_S12": (1, 12)}
PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
COLS = ["date", "open", "high", "low", "close", "volume"]


def read(path):
    return json.loads(Path(path).read_text())


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def get(url, params=None, missing=False):
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, proxies=PROXY, timeout=(8, 30))
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
            continue
        if r.status_code == 404 and missing:
            return None
        if r.status_code == 429:
            time.sleep(min(30, int(r.headers.get("Retry-After", "10"))))
        elif r.status_code < 500:
            r.raise_for_status()
            return r
        time.sleep(attempt + 1)
    r.raise_for_status()
    return r


def author(path):
    return subprocess.run(
        ["git", "-C", "/Users/chrise/GitHub/NostalgiaForInfinity", "show", PIN + ":" + path],
        capture_output=True,
        check=True,
    ).stdout


def freeze():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("Experiment already frozen")
    OUT.mkdir()
    parent = read(PARENT)
    original = read(ROOT / "research/crypto/nfi_original_reproduction_plan_v001.json")
    source = Path(original["datadir"])
    markets = read(parent["original_full_market_snapshot"]["path"])

    def parse(b):
        return rapidjson.loads(b, parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS)

    universe_blob = author("configs/pairlist-backtest-static-binance-futures-usdt.json")
    blacklist_blob = author("configs/blacklist-binance.json")
    (HERE / "author-universe.json").write_bytes(universe_blob)
    (HERE / "author-blacklist.json").write_bytes(blacklist_blob)
    from freqtrade.plugins.pairlist.pairlist_helpers import expand_pairlist

    universe = parse(universe_blob)["exchange"]["pair_whitelist"]
    blocked = set(expand_pairlist(parse(blacklist_blob)["exchange"]["pair_blacklist"], universe))
    clock = get("https://fapi.binance.com/fapi/v1/time").json()["serverTime"]
    end = pd.to_datetime(clock, unit="ms", utc=True).floor("5min")
    rows, excluded = [], {}
    for pair in universe:
        market = markets["markets"].get(pair, {})
        stem = pair.replace("/", "_").replace(":", "_")
        path = source / "futures" / f"{stem}-5m-futures.feather"
        if (
            pair in blocked
            or not market.get("active")
            or not markets["leverage_tiers"].get(pair)
            or not path.exists()
        ):
            excluded[pair] = "author blacklist or unavailable frozen market/data"
            continue
        frame = pd.read_feather(path)
        last = frame.loc[(frame.date >= START - pd.Timedelta(days=1)) & (frame.date < START)]
        history = frame.loc[(frame.date < START - pd.Timedelta(days=60)) & (frame.volume > 0)]
        expected = pd.date_range(START - pd.Timedelta(days=1), START, freq="5min", inclusive="left")
        if history.empty or not pd.DatetimeIndex(last.date).as_unit("ns").equals(
            expected.as_unit("ns")
        ):
            excluded[pair] = "pre-2026 volume/history coverage unavailable"
            continue
        values = last[["high", "low", "close", "volume"]].to_numpy()
        if not np.isfinite(values).all() or (last.close <= 0).any() or (last.volume < 0).any():
            raise ValueError("Invalid historical volume input")
        volume = float((((last.high + last.low + last.close) / 3) * last.volume).sum())
        rows.append({"pair": pair, "estimated_quote_volume": volume, "source": ref(path)})
    rows.sort(key=lambda x: (-x["estimated_quote_volume"], x["pair"]))
    if len(rows) < 80:
        raise ValueError(f"Only {len(rows)} eligible historical coins")
    pairs = [r["pair"] for r in rows[:80]]
    save(HERE / "universe.json", {"ranked_inputs": rows, "excluded": excluded, "selected": pairs})
    save(
        OUT / "market-rules.json",
        {
            **markets,
            "markets": {p: markets["markets"][p] for p in pairs},
            "leverage_tiers": {p: markets["leverage_tiers"][p] for p in pairs},
        },
    )
    save(
        PROTOCOL,
        {
            "schema": "freqtrade.research.nfi80-capacity.v1",
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "request": "Original X8 signals, 80 coins, test 3x and 12 slots against leverage/slot controls, 2026 YTD",
            "start": START.isoformat(),
            "end": end.isoformat(),
            "comparison_end": (end - pd.Timedelta(minutes=5)).isoformat(),
            "pairs": pairs,
            "universe": ref(HERE / "universe.json"),
            "source_datadir": str(source),
            "universe_rule": "Author backtest universe and blacklist, >=60d observed pre-start history; top80 prior-day typical-price*base-volume proxy (same candle quote-volume formula as VolumePairList). Freeze once before economics; StaticPairList during replay.",
            "selection_is_not_native_dynamic_volume_pairlist": True,
            "market_rules": ref(OUT / "market-rules.json"),
            "cases": CASES,
            "costs_bps_per_side": [15, 8],
            "initial_usdt": 1000,
            "research_drawdown_limit": 0.5,
            "new_contract_not_a_revision_of_prior_failed_gates": True,
            "data_policy": "Official checksummed archives; public GET tail; no synthetic candles or zero-filled funding. Keep raw funding millisecond clocks; native hourly model permits <1s floor only.",
            "source_policy": "Unchanged pinned NFI and Freqtrade matching/wallet. Lossless compressed research storage and read-only 5m mark observations only.",
            "source_code": ref(Path(__file__)),
            "strategy_commit": PIN,
            "financial_workers": 1,
            "limitations": [
                "Retrospective source version and current market metadata; not independent OOS or PIT membership proof",
                "Static prior-day 80-coin basket, not a dynamically refreshed year-long universe",
                "15/8bps are fee+slippage proxies; funding and liquidation remain native model evidence",
                "Five-minute mark observations omit intrabar worst-case risk",
            ],
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        json.dumps({"frozen_pairs": 80, "end": end.isoformat(), "eligible": len(rows)}), flush=True
    )


def archive(symbol, family, period, folder, receipts):
    funding = family == "fundingRate"
    grain = "monthly" if len(period) == 7 else "daily"
    name = f"{symbol}-fundingRate-{period}.zip" if funding else f"{symbol}-5m-{period}.zip"
    segment = f"{grain}/{family}/{symbol}/" + ("" if funding else "5m/") + name
    url = "https://data.binance.vision/data/futures/um/" + segment
    path = folder / f"{family}-{period}.zip"
    check = path.with_suffix(".zip.CHECKSUM")
    if not path.exists():
        checksum = get(url + ".CHECKSUM", missing=True)
        if checksum is None:
            receipts.append({"url": url, "state": "NOT_PUBLISHED"})
            return None
        body = get(url).content
        if hashlib.sha256(body).hexdigest() != checksum.text.split()[0]:
            raise ValueError("Official archive checksum mismatch")
        path.write_bytes(body)
        check.write_bytes(checksum.content)
    if sha(path) != check.read_text().split()[0]:
        raise ValueError("Cached archive changed")
    receipts.append({"url": url, **ref(path)})
    with ZipFile(path) as z:
        if len(z.namelist()) != 1:
            raise ValueError("Unexpected archive shape")
        raw = z.read(z.namelist()[0])
    if funding:
        f = pd.read_csv(io.BytesIO(raw))
        return pd.DataFrame(
            {
                "raw_time": f.calc_time.astype("int64"),
                "funding_rate": f.last_funding_rate.astype(float),
            }
        )
    f = pd.read_csv(io.BytesIO(raw), header=None)
    if str(f.iloc[0, 0]).startswith("open_time"):
        f = f.iloc[1:]
    n = f.iloc[:, :7].apply(pd.to_numeric, errors="raise")
    unit = "us" if n.iloc[0, 0] > 1e14 else "ms"
    return pd.DataFrame(
        {
            "date": pd.to_datetime(n[0], unit=unit, utc=True),
            **{k: n[i].astype(float) for i, k in enumerate(COLS[1:], 1)},
        }
    ).reset_index(drop=True)


def complete_prices(pair, family, grid, source, folder, receipts):
    symbol = pair.split(":")[0].replace("/", "")
    stem = pair.replace("/", "_").replace(":", "_")
    old = source / "futures" / f"{stem}-5m-futures.feather"
    data = (
        pd.read_feather(old).set_index("date")[COLS[1:]]
        if family == "klines"
        else pd.DataFrame(index=grid[:0], columns=COLS[1:], dtype=float)
    )
    for period in sorted(set(grid.difference(data.index).strftime("%Y-%m"))):
        f = archive(symbol, family, period, folder, receipts)
        if f is not None:
            extra = f.set_index("date")
            data = pd.concat([data, extra.loc[~extra.index.isin(data.index)]])
    today = pd.Timestamp(read(PROTOCOL)["end"]).normalize()
    for day in grid.difference(data.index).normalize().unique():
        if day >= today:
            continue
        f = archive(symbol, family, str(day.date()), folder, receipts)
        if f is not None:
            extra = f.set_index("date")
            data = pd.concat([data, extra.loc[~extra.index.isin(data.index)]])
    missing = grid.difference(data.index)
    endpoint = "klines" if family == "klines" else "markPriceKlines"
    # Repair only real, exact missing clocks, never manufacture/interpolate prices.
    for begin in range(0, len(missing), 1000):
        block = missing[begin : begin + 1000]
        # Large interior gaps need separate contiguous requests.
        groups = np.split(
            np.arange(len(block)), np.where(np.diff(block.as_unit("ms").asi8) != 300_000)[0] + 1
        )
        for group in groups:
            dates = block[group]
            rows = get(
                "https://fapi.binance.com/fapi/v1/" + endpoint,
                {
                    "symbol": symbol,
                    "interval": "5m",
                    "startTime": int(dates[0].timestamp() * 1000),
                    "endTime": int(dates[-1].timestamp() * 1000),
                    "limit": 1000,
                },
            ).json()
            p = folder / f"{family}-api-{int(dates[0].timestamp())}.json"
            save(p, rows)
            receipts.append(ref(p))
            f = pd.DataFrame(
                {
                    "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
                    **{k: [float(r[i]) for r in rows] for i, k in enumerate(COLS[1:], 1)},
                }
            ).set_index("date")
            if not f.index.as_unit("ns").equals(dates.as_unit("ns")):
                raise ValueError("Public API did not repair exact requested clocks")
            data = pd.concat([data, f])
    if data.index.has_duplicates:
        raise ValueError("Duplicate price timestamps")
    data = data.sort_index().reindex(grid)
    if (
        not np.isfinite(data.to_numpy()).all()
        or (data[["open", "high", "low", "close"]] <= 0).any().any()
        or (data.volume < 0).any()
    ):
        raise ValueError("Incomplete or invalid price calendar")
    return data.reset_index(names="date")


def funding(pair, end, folder, receipts):
    symbol = pair.split(":")[0].replace("/", "")
    parts = []
    for month in pd.period_range(START.tz_localize(None), end.tz_localize(None), freq="M"):
        if month.start_time >= end.tz_localize(None).replace(day=1, hour=0, minute=0, second=0):
            break
        part = archive(symbol, "fundingRate", str(month), folder, receipts)
        if part is None:
            raise ValueError("Historical funding archive unavailable")
        parts.append(part)
    cursor = int(end.normalize().replace(day=1).timestamp() * 1000)
    for page in range(10):
        rows = get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": int(end.timestamp() * 1000) - 1,
                "limit": 1000,
            },
        ).json()
        if not isinstance(rows, list):
            raise ValueError("Invalid public funding response")
        p = folder / f"funding-api-{page}.json"
        save(p, rows)
        receipts.append(ref(p))
        if rows:
            parts.append(
                pd.DataFrame(
                    {
                        "raw_time": [r["fundingTime"] for r in rows],
                        "funding_rate": [float(r["fundingRate"]) for r in rows],
                    }
                )
            )
        if len(rows) < 1000:
            break
        cursor = rows[-1]["fundingTime"] + 1
    else:
        raise ValueError("Funding pagination exceeded limit")
    raw = pd.concat(parts).sort_values("raw_time").reset_index(drop=True)
    raw["raw_date"] = pd.to_datetime(raw.raw_time, unit="ms", utc=True)
    raw = raw.loc[(raw.raw_date >= START) & (raw.raw_date < end)].copy()
    raw["date"] = raw.raw_date.dt.floor("h")
    offset = (raw.raw_date - raw.date).dt.total_seconds()
    if (
        raw.empty
        or raw.date.duplicated().any()
        or offset.max() >= 1
        or not np.isfinite(raw.funding_rate).all()
    ):
        raise ValueError("Unsupported or incomplete funding clocks")
    gaps = raw.date.diff().dropna()
    if max(raw.date.iloc[0] - START, end - raw.date.iloc[-1], gaps.max()) > pd.Timedelta(hours=9):
        raise ValueError("Funding coverage has a gap over nine hours")
    raw.to_parquet(folder / "funding-original-clocks.parquet", index=False)
    return raw[["date", "funding_rate"]].reset_index(drop=True)


def prepare_pair(pair):
    policy = read(PROTOCOL)
    end = pd.Timestamp(policy["end"])
    symbol = pair.split(":")[0].replace("/", "")
    folder = OUT / "raw" / symbol
    folder.mkdir(parents=True, exist_ok=True)
    done = folder / "completed.json"
    if done.exists():
        record = read(done)
        for name, digest in record["candles"].items():
            if sha(OUT / "candles" / name) != digest:
                raise ValueError("Prepared input changed")
        return record
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.enums import CandleType

    handler = FeatherDataHandler(OUT / "candles")
    source = Path(policy["source_datadir"])
    receipts = []
    grid = pd.date_range(WARM, end, freq="5min", inclusive="left")
    prices = complete_prices(pair, "klines", grid, source, folder, receipts)
    marks = complete_prices(
        pair,
        "markPriceKlines",
        pd.date_range(START, end, freq="5min", inclusive="left"),
        source,
        folder,
        receipts,
    )
    stem = pair.replace("/", "_").replace(":", "_")
    handler.ohlcv_store(pair, "5m", prices, CandleType.FUTURES)
    handler.ohlcv_store(pair, "5m", marks, CandleType.MARK)
    for tf, freq, start in [
        ("15m", "15min", "2025-11-01"),
        ("1h", "h", "2025-09-01"),
        ("4h", "4h", "2025-06-01"),
        ("1d", "D", "2025-01-01"),
    ]:
        original = pd.read_feather(source / "futures" / f"{stem}-{tf}-futures.feather")
        prefix = original.loc[
            (original.date >= pd.Timestamp(start, tz="UTC")) & (original.date < WARM)
        ]
        groups = prices.set_index("date").resample(freq)
        new = groups.agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        expected = int(
            pd.Timedelta(freq if freq not in ("h", "D") else "1" + freq) / pd.Timedelta(minutes=5)
        )
        new = new.loc[groups.close.count() == expected].reset_index()
        combined = pd.concat([prefix, new]).reset_index(drop=True)
        if combined.date.duplicated().any():
            raise ValueError("Informative overlap")
        handler.ohlcv_store(pair, tf, combined, CandleType.FUTURES)
    grouped = marks.set_index("date").resample("h")
    hourly = grouped.agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    # Native funding requires every observed hour including the partial current hour's known OPEN.
    handler.ohlcv_store(pair, "1h", hourly.reset_index(), CandleType.MARK)
    fund = funding(pair, end, folder, receipts)
    handler.ohlcv_store(pair, "1h", fund, CandleType.FUNDING_RATE)
    save(folder / "receipts.json", receipts)
    files = {
        str(p.relative_to(OUT / "candles")): sha(p)
        for p in (OUT / "candles/futures").glob(stem + "-*.feather")
    }
    record = {
        "pair": pair,
        "price_bars": len(prices),
        "mark_bars": len(marks),
        "funding_events": len(fund),
        "receipts": ref(folder / "receipts.json"),
        "candles": files,
    }
    save(done, record)
    return record


def prepare():
    policy = read(PROTOCOL)
    results = {}
    (OUT / "candles/futures").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(prepare_pair, p): p for p in policy["pairs"]}
        for future in as_completed(jobs):
            pair = jobs[future]
            try:
                results[pair] = future.result()
            except Exception as error:
                save(
                    OUT / "intake-failure.json",
                    {"pair": pair, "error": type(error).__name__, "detail": str(error)},
                )
                raise
            save(
                OUT / "intake-progress.json", {"completed": len(results), "total": 80, "last": pair}
            )
            print(json.dumps({"prepared": len(results), "pair": pair}), flush=True)
    receipt = {name: digest for row in results.values() for name, digest in row["candles"].items()}
    save(OUT / "data-receipts.json", receipt)
    save(OUT / "admission.json", results)


def register():
    policy = read(PROTOCOL)
    data = read(OUT / "admission.json")
    if set(data) != set(policy["pairs"]):
        raise ValueError("80-coin data admission incomplete")
    parent = read(PARENT)
    template = next(c for c in parent["candidates"] if c["name"] == "nfi-x8-1x")
    runner = (PARENT.parent / "run_wide_comparison.py").read_text()
    runner = runner.replace(
        'storage.DiskMapping(cache / f"analyzed_{serial}", 6)',
        'storage.DiskMapping(cache / f"analyzed_{serial}", 14)',
    )
    runner = (
        runner.replace("current_time.minute == 0", "True")
        .replace('freq="h"', 'freq="5min"')
        .replace("hourly_mark_equity", "marked_equity")
        .replace("hourly_equity.parquet", "five_minute_equity.parquet")
    )
    runner = runner.replace(
        "part.date.reset_index(drop=True).equals(pd.Series(expected))",
        'part.date.dt.as_unit("ns").reset_index(drop=True).equals(pd.Series(expected.as_unit("ns")))',
    )
    (HERE / "native_runner.py").write_text(runner)
    observer = (
        Path(parent["observer_source"]["path"])
        .read_text()
        .replace('("1h-mark", self.marks)', '("5m-mark", self.marks)')
        .replace("if current_time.minute == 0:", "if True:")
        .replace("hourly_mark_equity", "marked_equity")
        .replace(
            "    self.current = row",
            '    row["open_margin_usdt"] = math.fsum(t.stake_amount for t in LocalTrade.bt_trades_open)\n    self.current = row',
        )
    )
    (HERE / "observer.py").write_text(observer)
    plans = {}
    for name, (leverage, slots) in CASES.items():
        path = HERE / f"{name}.json"
        if path.exists():
            raise FileExistsError("Native plan already registered")
        config = read(template["config"]["path"])
        config.update(
            max_open_trades=slots, dry_run=True, dry_run_wallet=1000, stake_amount="unlimited"
        )
        config["exchange"].update(
            pair_whitelist=policy["pairs"], pair_blacklist=[], key="", secret="", password=""
        )
        config["nfi_parameters"] = {
            k: float(leverage)
            for k in (
                "futures_mode_leverage",
                "futures_mode_leverage_rebuy_mode",
                "futures_mode_leverage_grind_mode",
            )
        }
        save(HERE / f"{name}-config.json", config)
        candidate = {
            **template,
            "name": name,
            "max_open_trades": slots,
            "config": ref(HERE / f"{name}-config.json"),
            "source_and_risk_behavior": "Unchanged X8; only registered leverage and slots",
        }
        end = policy["comparison_end"]
        plan = {
            **parent,
            "schema": "freqtrade.research.nfi80-capacity-native.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "start": policy["start"],
            "end": policy["end"],
            "comparison_end": end,
            "pairs": policy["pairs"],
            "datadir": str(OUT / "candles"),
            "data_receipts": ref(OUT / "data-receipts.json"),
            "original_dataset_receipts": ref(OUT / "data-receipts.json"),
            "market_snapshot": policy["market_rules"],
            "marks": {
                p: ref(
                    OUT
                    / "candles/futures"
                    / (p.replace("/", "_").replace(":", "_") + "-1h-mark.feather")
                )
                for p in policy["pairs"]
            },
            "candidates": [candidate],
            "allowed_leverage": float(leverage),
            "total_cost_bps_per_side": [15, 8],
            "runner_sha256": sha(HERE / "native_runner.py"),
            "observer_source": ref(HERE / "observer.py"),
            "storage_source": ref(HERE / "compressed_storage.py"),
            "memory_guard_bytes": 6 * 1024**3,
            "wall_guard_seconds": 10800,
            "periods": [
                {"name": "continuous", "start": policy["start"], "end": end},
                {"name": "2026-H1", "start": policy["start"], "end": "2026-07-01T00:00:00Z"},
                {"name": "2026-H2-to-cutoff", "start": "2026-07-01T00:00:00Z", "end": end},
            ],
            "known_limitations": policy["limitations"],
            "orders_enabled": False,
            "production_eligible": False,
        }
        save(path, plan)
        plans[name] = ref(path)
    save(HERE / "native-plans.json", plans)


def run():
    plans = read(HERE / "native-plans.json")
    all_results = {}
    for cost in (15, 8):
        for name in CASES:
            if sha(Path(plans[name]["path"])) != plans[name]["sha256"]:
                raise ValueError("Frozen native account plan changed")
            key = f"{name}_{cost}bps"
            folder = OUT / key
            if not (folder / "result.json").exists():
                if folder.exists():
                    raise FileExistsError(
                        "Failed/partial account retained; inspect before resuming"
                    )
                if shutil.disk_usage(OUT).free < 5 * 1024**3:
                    raise OSError("Insufficient free disk for native replay")
                with (OUT / (key + ".log")).open("x") as log:
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(HERE / "native_runner.py"),
                            "--plan",
                            plans[name]["path"],
                            "--candidate",
                            name,
                            "--cost-bps",
                            str(cost),
                            "--output",
                            str(folder),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                save(
                    folder / "completion.json",
                    {
                        "returncode": child.returncode,
                        "finished_at_utc": datetime.now(UTC).isoformat(),
                        "orders_enabled": False,
                    },
                )
                if child.returncode:
                    raise RuntimeError(f"Native account failed: {key}; see preserved log")
            result = read(folder / "result.json")
            all_results[key] = result
            save(OUT / "matrix-progress.json", {"completed": list(all_results), "total": 8})
            print(
                json.dumps(
                    {
                        "completed_account": key,
                        "seconds": result["elapsed_seconds"],
                        "return": result["periods"][0]["net_return"],
                    }
                ),
                flush=True,
            )
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                count = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                shutil.rmtree(cache)
                save(
                    folder / "generated-cache-cleanup.json",
                    {
                        "bytes_removed": count,
                        "scope": "Only this successful account's generated lossless cache; native archives and equity retained",
                    },
                )
    save(
        HERE / "result.json",
        {
            "protocol": ref(PROTOCOL),
            "accounts": all_results,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "prepare", "register", "run"])
    arguments = parser.parse_args()
    globals()[arguments.action]()
