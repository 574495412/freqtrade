"""Revalue the matched temporal accounts on official five-minute marks."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.listing_reversal_20261107 import catalog, intake
from research.crypto.nfi_strength_allocation_20261107 import temporal
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, HERE, ROOT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


OUT = DATA / "nfi_flat_mark_20261107_v001"
REGISTRATION = HERE / "mark_registration.json"
RESULT = HERE / "temporal_result.json"
ORACLE = (
    ROOT
    / "docs/research-20261105/crypto-wide-comparison-20261105"
    / "reconstruct-five-minute-reference-v002.py"
)
MARK_URL = "https://fapi.binance.com/fapi/v1/markPriceKlines"


def register():
    if OUT.exists() or REGISTRATION.exists():
        raise FileExistsError("Mark revaluation already registered")
    save(
        REGISTRATION,
        {
            "sources": [
                record(path)
                for path in (
                    RESULT,
                    temporal.REGISTRATION,
                    ORACLE,
                    Path(__file__),
                    Path(catalog.__file__),
                    Path(intake.__file__),
                )
            ],
            "max_native_hourly_nav_error_usdt": 0.00001,
            "mark_source": "Binance official monthly/daily marks with REST gap recovery",
            "policy": (
                "Use the same trade fills and model funding ledger. First require independent "
                "hourly NAV parity, then use 5m official mark OPEN to measure observed "
                "drawdown, with a 1 percentage point difference budget. No new economic account."
            ),
            "orders_enabled": False,
        },
    )


def month_mark(pair, grid):
    symbol = pair.split(":")[0].replace("/", "")
    folder = OUT / "marks" / symbol
    folder.mkdir(parents=True)
    receipts = []
    pieces = [
        intake.archive(symbol, "markPriceKlines", period, folder, receipts)
        for period in sorted(set(grid.strftime("%Y-%m")))
    ]
    frames = [f.set_index("date") for f in pieces if f is not None]
    data = pd.concat(frames).open if frames else pd.Series(dtype=float, index=grid[:0])
    data.index = data.index.as_unit("us")
    for day in grid.difference(data.index).normalize().unique():
        chunk = intake.archive(symbol, "markPriceKlines", str(day.date()), folder, receipts)
        if chunk is not None:
            extra = chunk.set_index("date").open
            extra.index = extra.index.as_unit("us")
            data = pd.concat([data, extra.loc[~extra.index.isin(data.index)]])
    missing = grid.difference(data.index)
    for offset in range(0, len(missing), 900):
        block = missing[offset : offset + 900]
        # Gaps can be far apart. Query only contiguous runs, each within API limit.
        for group in np.split(
            np.arange(len(block)), np.where(np.diff(block.asi8) != 300_000_000)[0] + 1
        ):
            dates = block[group]
            t0, t1 = (int(t.timestamp() * 1000) for t in (dates[0], dates[-1]))
            raw = folder / f"api-{t0}.json"
            raw.write_bytes(
                catalog.fetch(
                    MARK_URL,
                    {
                        "symbol": symbol,
                        "interval": "5m",
                        "startTime": t0,
                        "endTime": t1,
                        "limit": 1000,
                    },
                )
            )
            rows = read(raw)
            clocks = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True).as_unit("us")
            if not clocks.equals(dates) or any(r[6] != r[0] + 299_999 for r in rows):
                raise ValueError(f"API did not fill an exact mark gap: {pair} {dates[0]}")
            data = pd.concat([data, pd.Series([float(r[1]) for r in rows], index=clocks)])
            receipts.append({"file": raw.name, "sha256": sha(raw)})
    if data.index.has_duplicates:
        raise ValueError(f"Duplicate mark clock: {pair}")
    data = data.reindex(grid)
    if not np.isfinite(data).all() or (data <= 0).any():
        raise ValueError(f"Incomplete mark grid: {pair}")
    save(folder / "receipts.json", receipts)
    return pair, data, {"rows": len(data), "receipts_sha256": sha(folder / "receipts.json")}


def trades_and_checks(result, plan, oracle, hour):
    receipts = read(checked(plan["data_receipts"]))
    store = Path(plan["datadir"])
    holdings = {}
    for name, row in result["accounts"].items():
        (path,) = (temporal.OUT / name / "native_results").glob("*.zip")
        checked({"path": str(path), "sha256": row["archive_sha256"]})
        with ZipFile(path) as handle:
            (key,) = [
                s
                for s in handle.namelist()
                if s.endswith(".json") and not s.endswith("_config.json")
            ]
            (stats,) = json.loads(handle.read(key))["strategy"].values()
        holdings[name] = stats["trades"]
    pairs = sorted({t["pair"] for trades in holdings.values() for t in trades})
    sources = {
        pair: (
            oracle.verified_frame(store, receipts, pair, "1h-funding_rate"),
            oracle.verified_frame(store, receipts, pair, "1h-mark"),
        )
        for pair in pairs
    }
    checks = {}
    for name, trades in holdings.items():
        nav = np.full(len(hour), plan["initial_equity_usdt"], dtype=float)
        clocks = hour.as_unit("ms").asi8
        for trade in trades:
            fund, mark = sources[trade["pair"]]
            prices = mark.set_index("date").open.reindex(hour).to_numpy()
            oracle.contribute(trade, clocks, prices, fund, mark, nav)
        archived = pd.read_parquet(checked(result["accounts"][name]["hourly_equity"]))
        observed = archived.set_index("date").hourly_mark_equity.reindex(hour)
        if observed.isna().any() or not np.isfinite(nav).all():
            raise ValueError("Archived hourly observations are incomplete")
        checks[name] = float(np.max(np.abs(nav - observed.to_numpy())))
    return holdings, sources, checks


def run():
    policy = read(REGISTRATION)
    for source in policy["sources"]:
        checked(source)
    result, plan = read(RESULT), read(temporal.REGISTRATION)
    oracle = load_module("frozen_hourly_oracle", ORACLE)
    hour = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("us")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="5min").as_unit("us")
    OUT.mkdir()
    holdings, sources, checks = trades_and_checks(result, plan, oracle, hour)
    save(OUT / "hourly_parity.json", checks)
    if max(checks.values()) > policy["max_native_hourly_nav_error_usdt"]:
        raise ValueError(f"Native hourly NAV failed independent parity: {checks}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        marked = list(pool.map(lambda s: month_mark(s, grid), sources))
    marks = {symbol: data for symbol, data, _ in marked}
    for symbol, marks5 in marks.items():
        native = sources[symbol][1].set_index("date").open.reindex(hour).to_numpy()
        if not np.allclose(marks5.reindex(hour).to_numpy(), native, rtol=1e-10, atol=1e-12):
            raise ValueError(f"Five-minute marks and parent marks differ at hourly grid: {symbol}")
    reports = {}
    for name, trades in holdings.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in trades:
            symbol = trade["pair"]
            fund, mark = sources[symbol]
            oracle.contribute(
                trade, grid.as_unit("ms").asi8, marks[symbol].to_numpy(), fund, mark, nav
            )
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = OUT / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        reports[name] = {
            "nav_points": len(nav),
            "drawdown_5m_mark": float(dd.max()),
            "nav_sha256": sha(path),
            "end_equity": float(nav[-1]),
            "hourly_parity_error_usdt": checks[name],
        }
    for cost in (8, 15):
        candidate = reports[f"FlatTopBoost_{cost}bps"]
        original = reports[f"nfi-x8-1x_{cost}bps"]
        candidate["drawdown_uplift"] = candidate["drawdown_5m_mark"] - original["drawdown_5m_mark"]
        candidate["within_1pp_risk_budget"] = candidate["drawdown_uplift"] <= 0.01
    save(
        HERE / "mark_result.json",
        {
            "registration": record(REGISTRATION),
            "mark_receipts": {s: r for s, _, r in marked},
            "accounts": reports,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
