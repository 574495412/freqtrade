"""Test sensitivity of the inventory factor to native simultaneous signal order."""

import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_solo_allocation_20261107 import run as study
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades
from research.crypto.nfi_strength_allocation_20261107.register import PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


PLAN = study.HERE / "reverse_order_registration.json"
OUT = study.OUT / "reverse_order"


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Reverse-order sensitivity already registered")
    policy = study.read(study.SHORT_PLAN)
    control = study.read(study.A04 / "temporal_registration.json")
    base = next(c for c in control["candidates"] if c["name"] == "nfi-x8-1x")
    (candidate,) = policy["candidates"]
    pairs = list(reversed(policy["pairs"]))
    cases = []
    for case in (base, candidate):
        raw = study.read(checked(case["config"]))
        raw["exchange"]["pair_whitelist"] = pairs
        path = study.HERE / f"{case['name']}_reverse_config.json"
        save(path, raw)
        cases.append({**case, "config": record(path)})
    save(
        PLAN,
        {
            **policy,
            "schema": "freqtrade.research.nfi-inventory-reverse-order.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "pairs": pairs,
            "candidates": cases,
            "comparison": (
                "Reverse the identical pair list for both original and candidate. Use the same "
                "signal/exit/wallet code, 65-day dates, costs and 5m mark observation. No use as "
                "fresh data. Preserve the primary order and report the paired net excess and "
                "the pre-existing +1pp drawdown budget."
            ),
            "sources": [
                record(path)
                for path in (
                    Path(__file__),
                    study.SHORT_PLAN,
                    study.REGISTRATION,
                    study.A04 / "temporal_registration.json",
                    Path(temporal.__file__),
                    Path(mark_valuation.__file__),
                    mark_valuation.ORACLE,
                )
            ],
            "orders_enabled": False,
        },
    )


def native_accounts(plan):
    reports = {}
    for cost in (15, 8):
        for case in plan["candidates"]:
            key = f"{case['name']}_{cost}bps"
            folder = OUT / key
            with (OUT / f"{key}.log").open("x") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(PARENT / "run_wide_comparison.py"),
                        "--plan",
                        str(PLAN),
                        "--candidate",
                        case["name"],
                        "--cost-bps",
                        str(cost),
                        "--output",
                        str(folder),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            summary = temporal.audit_one(plan, case, cost, folder)
            if case["name"] == study.STRATEGY:
                trades, _ = native_trades(plan, folder, case["strategy"], cost)
                summary.update(study.audit_inventory(folder, trades))
            save(folder / "audit.json", summary)
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                shutil.rmtree(cache)
            reports[key] = summary
            print(key, summary["periods"]["continuous"]["net_return"], flush=True)
    return reports


def risk_replay(plan, reports):
    temporal.OUT = OUT
    mark_valuation.OUT = OUT / "5m"
    oracle = load_module("frozen_reverse_order_oracle", mark_valuation.ORACLE)
    hour = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("us")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="5min").as_unit("us")
    holdings, sources, checks = mark_valuation.trades_and_checks(
        {"accounts": reports}, plan, oracle, hour
    )
    if max(checks.values()) > 1e-5:
        raise ValueError("Native hourly parity did not hold")
    with ThreadPoolExecutor(max_workers=8) as pool:
        marked = list(pool.map(lambda symbol: mark_valuation.month_mark(symbol, grid), sources))
    prices = {s: f for s, f, _ in marked}
    for symbol, values in prices.items():
        prior = sources[symbol][1].set_index("date").open.reindex(hour).to_numpy()
        if not np.allclose(values.reindex(hour), prior, atol=1e-12, rtol=1e-10):
            raise ValueError(f"Official five-minute marks disagree at hourly clocks: {symbol}")
    risk = {}
    for key, trades in holdings.items():
        net = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in trades:
            symbol = trade["pair"]
            fund, mark = sources[symbol]
            oracle.contribute(
                trade, grid.as_unit("ms").asi8, prices[symbol].to_numpy(), fund, mark, net
            )
        dd = 1 - net / np.maximum.accumulate(net)
        path = OUT / "5m" / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": net, "drawdown": dd}).to_parquet(path, index=False)
        risk[key] = {
            "mark_drawdown": float(dd.max()),
            "nav_points": len(net),
            "curve_sha256": sha(path),
            "hourly_parity_error_usdt": checks[key],
        }
    return risk, {s: info for s, _, info in marked}


def run():
    plan = study.read(PLAN)
    for source in plan["sources"]:
        checked(source)
    OUT.mkdir()
    reports = native_accounts(plan)
    risk, marks = risk_replay(plan, reports)
    comparison = {}
    for cost in (15, 8):
        base = f"nfi-x8-1x_{cost}bps"
        selected = f"{study.STRATEGY}_{cost}bps"
        a, b = (reports[key]["periods"]["continuous"]["net_return"] for key in (base, selected))
        d0, d1 = (risk[key]["mark_drawdown"] for key in (base, selected))
        comparison[str(cost)] = {
            "original_net_return": a,
            "candidate_net_return": b,
            "return_uplift": b - a,
            "original_5m_drawdown": d0,
            "candidate_5m_drawdown": d1,
            "drawdown_uplift": d1 - d0,
            "passes_direction_and_risk_check": b > max(0, a) and d1 - d0 <= 0.01,
        }
    save(
        study.HERE / "reverse_order_result.json",
        {
            "registration": record(PLAN),
            "accounts": reports,
            "risk": risk,
            "mark_sources": marks,
            "comparison": comparison,
            "paper_eligible": False,
        },
    )
    print(comparison, flush=True)


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
