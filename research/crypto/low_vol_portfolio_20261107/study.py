"""Frozen data admission and source replication for the 63-day low-volatility book."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from research.crypto.low_vol_portfolio_20261107 import account
from research.crypto.nfi_btc_leverage_20261107 import study as leverage
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "low_vol_portfolio_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
ORIGINAL = Path("/Users/chrise/GitHub/Vibe-Trading/research/core_factor_price_diagnostic")
ORIGINAL_ACCT = ORIGINAL / "result/accounts_0m"
FACTOR = ORIGINAL.parents[1] / "agent/src/factors/zoo/core/low_volatility_63.py"
API = "https://fapi.binance.com/fapi/v1/fundingRate"
FACTOR_ID = "core_low_volatility_63"
WINDOWS = {
    "2024": ("2024-01-01", "2024-12-30"),
    "earlier_2025": ("2025-01-06", "2025-09-15"),
    "original": ("2025-09-15", "2026-08-31"),
}
COSTS = (13, 25)


def clock(day):
    return pd.Timestamp(day, tz="UTC") + pd.Timedelta(minutes=5)


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Portfolio already frozen")
    original = read(ORIGINAL_ACCT / "accounts.json")
    if sha(FACTOR) != original["factor_sources_sha256"][FACTOR_ID]:
        raise ValueError("Source factor differs from original account")
    parent = leverage.check()["2024_Native2x"]
    refs = (
        Path(__file__),
        Path(account.__file__),
        FACTOR,
        parent,
        ORIGINAL_ACCT / "accounts.json",
        ORIGINAL_ACCT / "trades.csv",
        leverage.a13.OUT / "5m/admission.json",
        long_mark.OUT / "admission.json",
    )
    save(
        POLICY,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(p) for p in refs],
            "parent": record(parent),
            "symbols": original["symbols"],
            "windows": WINDOWS,
            "source_parity": (
                "Before funding, rerun the original 50 weekly ranks and 13bps unrounded "
                "1x ledger, compare exact signed portfolios, NAV/return and execution prices."
            ),
            "funded_experiment": (
                "Same 25 perpetuals, 63 daily simple-return sample SD, long lowest 5 and short "
                "highest 5, equal signed absolute weights and alphabetical ties. Read "
                "completed Sunday close only, Monday 00:05 trade OPEN proxy, weekly rebalance. "
                "Each window resets to 1000 USDT. Close at final Monday. 1x/2x total gross "
                "targets after costs, one pooled cross-margin model with 2x per-instrument "
                "margin, current snapshot lot/minimum/first-tier maintenance. Charge "
                "13/25bps on actual change in notional. Missing or excessive order is a "
                "recorded rejection. Batch cap = 1% of average completed 5m base volume "
                "over prior hour; terminal/stop closes are modeled and excess is flagged. "
                "Exact public rate times and settlement mark prices "
                "must match official archived events. Apply at first 5m mark clock at or "
                "after the event, before trades at that clock. Do not treat a missing event "
                "as observed zero; affected outcomes only partial and ineligible."
            ),
            "risk": (
                "Estimate marked NAV and close-cost reserve each 5m. At observed 30% "
                "drawdown or modeled maintenance breach halt and flatten next 5m open. "
                "No live fill/liquidation guarantee. 5m open may miss intrabar extremes. "
                "A rebalancing batch is atomic only in the model."
            ),
            "gate": (
                "2x at both costs in all 3 windows must have zero missing held settlements, "
                "modeled margin breaches, halt and rejected batches, absolute 5m DD <=25%, "
                "positive net and >=25% annualized net, >=1.5x the 1x net gain and "
                "return/DD >= 1x. 2024 and earlier 2025 are separate retrospective "
                "checks, while original known returns do not count as new OOS."
            ),
            "orders_enabled": False,
        },
    )


def symbol_pair(symbol):
    return f"{symbol[:-4]}/USDT:USDT"


def candles(plan, symbol):
    root = Path(plan["datadir"])
    receipts = read(checked(plan["data_receipts"]))
    name = f"futures/{symbol[:-4]}_USDT_USDT-5m-futures.feather"
    path = checked({"path": str(root / name), "sha256": receipts[name]})
    df = pd.read_feather(path).set_index("date")
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise ValueError("Invalid candle grid")
    return df[["open", "close", "volume"]]


def funding(plan, symbol):
    root = Path(plan["datadir"]).parents[0]
    existing = root / f"{symbol}_funding_events.parquet"
    expected_sha = read(root / "receipts.json")[existing.name]
    source = checked({"path": str(existing), "sha256": expected_sha})
    archived = pd.read_parquet(source)
    start, end = clock(WINDOWS["2024"][0]), clock(WINDOWS["original"][1])
    lo, hi = (t.value // 1_000_000 for t in (start, end))
    raw = []
    receipts = []
    cursor = lo
    while cursor <= hi:
        url = (
            API
            + "?"
            + urlencode({"symbol": symbol, "startTime": cursor, "endTime": hi, "limit": 1000})
        )
        with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed official HTTPS API
            body = response.read()
        part = json.loads(body)
        if not isinstance(part, list) or any(r["symbol"] != symbol for r in part):
            raise ValueError("Unexpected public funding response")
        path = OUT / f"{symbol}_{cursor}_funding.json"
        path.write_bytes(body)
        receipts.append({"url": url, "response": record(path)})
        raw.extend(part)
        if len(part) < 1000:
            break
        cursor = int(part[-1]["fundingTime"]) + 1
    df = pd.DataFrame(raw)
    observed = df.fundingTime.to_numpy(dtype=np.int64)
    sealed = archived.loc[archived.calc_time.between(lo, hi)]
    rates = df.fundingRate.to_numpy(dtype=float)
    if not np.array_equal(observed, sealed.calc_time.to_numpy()) or not np.allclose(
        rates, sealed.funding_rate, rtol=0, atol=1e-12
    ):
        raise ValueError(f"Public API and independent archived funding differ: {symbol}")
    marks = df.markPrice.to_numpy(dtype=float)
    invalid = ~np.isfinite(marks) | (marks <= 0)
    buckets = pd.to_datetime(observed, unit="ms", utc=True).ceil("5min")
    missing = []
    for (previous, _), (now, hours) in pairwise(
        zip(observed, sealed.funding_interval_hours, strict=True)
    ):
        step = int(hours) * 3_600_000
        if step <= 0:
            continue
        nominal = ((now + 1_800_000) // 3_600_000) * 3_600_000
        missing.extend(t for t in range(nominal - step, previous + 60_000, -step))
    return (
        pd.Series(np.where(invalid, 0, marks * rates), index=buckets).groupby(level=0).sum(),
        list(pd.to_datetime(missing, unit="ms", utc=True)) + list(buckets[invalid]),
        {
            "events": len(df),
            "invalid_marks": int(invalid.sum()),
            "expected_missing": len(missing),
            "archive": record(source),
            "raw": receipts,
        },
    )


def weights(raw):
    data = {}
    for coin, df in raw.items():
        close = df.close.resample("D").last()
        complete = df.close.resample("D").count() == 288
        data[coin] = close.where(complete)
    volatility = pd.DataFrame(data).pct_change(fill_method=None).rolling(63).std(ddof=1)
    targets = {}
    for a, b in WINDOWS.values():
        for at in pd.date_range(clock(a), clock(b), freq="7D"):
            rank = volatility.loc[at.floor("D") - pd.Timedelta(days=1)]
            if not np.isfinite(rank).all() or rank.std() <= 0:
                raise ValueError(f"Original rank has missing inputs: {at}")
            order = sorted(rank.index, key=lambda s: (rank[s], s))
            low, high = set(order[:5]), set(order[-5:])
            targets[at] = np.array(
                [0.1 if s in low else -0.1 if s in high else 0 for s in rank.index]
            )
    return targets


def reproduce(raw, ranks):
    source = read(ORIGINAL_ACCT / "accounts.json")
    archive = pd.read_csv(ORIGINAL_ACCT / "trades.csv")
    archive = archive[
        (archive.factor == FACTOR_ID)
        & (archive.policy == "long_short")
        & (archive.scenario == "price_minus_assumed_cost_excluding_funding")
    ]
    first, end = (clock(d) for d in WINDOWS["original"])
    cash = source["initial_per_independent_case"]
    capital = cash
    qty = np.zeros(len(raw))
    target_archive = {}
    for time, rows in archive.groupby("time", sort=True):
        target_archive[pd.Timestamp(time)] = rows.set_index("symbol")
    price_error = qty_error = 0.0
    for at in pd.date_range(first, end, freq="7D"):
        prices = np.array([df.open.at[at] for df in raw.values()])
        target = np.zeros(len(raw)) if at == end else ranks[at]
        old = target_archive[at]
        for i, coin in enumerate(raw):
            if coin in old.index:
                price_error = max(price_error, abs(prices[i] - old.at[coin, "price"]))
                if target[i] != old.at[coin, "target_weight_after_cost"]:
                    raise ValueError(f"Original low-volatility rank differs: {coin} {at}")
        desired = account.post_cost_target(
            cash + qty @ prices, qty * prices, target, prices, 0.0013
        )
        for i, coin in enumerate(raw):
            if coin in old.index:
                qty_error = max(qty_error, abs(desired[i] - old.at[coin, "quantity_after"]))
        delta = desired - qty
        cash -= delta @ prices + np.abs(delta) @ prices * 0.0013
        qty = desired
    expected = next(
        row["conditional_return_fraction"]
        for row in source["summary"]
        if row["factor"] == FACTOR_ID
        and row["policy"] == "long_short"
        and row["scenario"] == "price_minus_assumed_cost_excluding_funding"
    )
    if price_error > 1e-10 or qty_error > 1e-6 or abs(cash / capital - 1 - expected) > 1e-9:
        raise ValueError(f"Original price-only reproduction failed: {price_error=} {qty_error=}")
    return {
        "original_price_only_return": expected,
        "reproduced_return": cash / capital - 1,
        "max_execution_price_difference": price_error,
        "max_quantity_difference": qty_error,
    }


def run():
    policy = read(POLICY)
    for ref in policy["sources"]:
        checked(ref)
    plan = read(checked(policy["parent"]))
    OUT.mkdir()
    raw = {symbol: candles(plan, symbol) for symbol in policy["symbols"]}
    ranks = weights(raw)
    parity = reproduce(raw, ranks)
    print("original parity", parity, flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        funded = dict(zip(raw, pool.map(lambda s: funding(plan, s), raw), strict=True))
    save(OUT / "funding_receipts.json", {k: r[2] for k, r in funded.items()})
    snap = read(checked(plan["market_snapshot"]))
    rules = account.snapshot(snap, [symbol_pair(s) for s in raw])
    report = {}
    for name, (a, b) in WINDOWS.items():
        parent = leverage.a13.OUT / "5m" if name == "2024" else long_mark.OUT
        admission = read(parent / "admission.json")["marks"]
        grid = pd.date_range(clock(a), clock(b), freq="5min")
        market = account.make_inputs(grid, raw, funded, admission, symbol_pair)
        accounts = {}
        for gross in (1, 2):
            for cost in COSTS:
                key = f"{name}_{gross}x_{cost}bps"
                accounts[key] = account.simulate(grid, market, ranks, rules, gross, cost, OUT / key)
                print(key, accounts[key]["net"], accounts[key]["drawdown"], flush=True)
        report[name] = accounts
    save(
        RESULT,
        {
            "protocol_sha256": sha(POLICY),
            "source_parity": parity,
            "funding_receipts": record(OUT / "funding_receipts.json"),
            "windows": report,
            "checks": account.assess(report),
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
