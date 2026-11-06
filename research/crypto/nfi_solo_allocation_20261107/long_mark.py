"""Acquire official 5m marks and revalue the frozen long-window A08 accounts."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi_solo_allocation_20261107 import run as study
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation
from research.crypto.nfi_strength_allocation_20261107.register import BASELINES, record
from research.crypto.nfi_strength_allocation_20261107.register import OUT as FLAT_ROOT
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


PLAN = study.HERE / "long_mark_registration.json"
OUT = study.OUT / "long_mark"


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Long mark check already registered")
    save(
        PLAN,
        {
            "registered_at": datetime.now(UTC).isoformat(),
            "sources": [
                record(p)
                for p in (
                    Path(__file__),
                    study.LONG_PLAN,
                    study.REGISTRATION,
                    study.A04 / "result.json",
                    Path(mark_valuation.__file__),
                    mark_valuation.ORACLE,
                )
            ],
            "criteria": (
                "Valuation-only, exactly the frozen six long-window native ledgers. Require "
                "independent hourly NAV parity <=1e-5 USDT before using the 5m marks. All "
                "required symbols must match official hourly mark openings. Require 5m "
                "drawdown at most 1pp higher than original and return/5m drawdown greater "
                "than fixed boost. Preserve unknown data and original results."
            ),
            "orders_enabled": False,
        },
    )


def grids(plan):
    return tuple(
        pd.date_range(plan["start"], plan["comparison_end"], freq=freq).as_unit("us")
        for freq in ("h", "5min")
    )


def acquire(plan):
    OUT.mkdir()
    _, grid = grids(plan)
    mark_valuation.OUT = OUT

    def load(symbol):
        try:
            _, prices, info = mark_valuation.month_mark(symbol, grid)
            filename = symbol.split(":")[0].replace("/", "") + ".parquet"
            path = OUT / filename
            prices.rename("open").to_frame().to_parquet(path)
            return symbol, {"status": "VERIFIED", **info, "frame": record(path)}
        except (OSError, ValueError) as error:
            return symbol, {"status": "UNKNOWN", "reason": str(error)}

    with ThreadPoolExecutor(max_workers=10) as pool:
        records = dict(pool.map(load, plan["pairs"]))
    save(
        OUT / "admission.json",
        {
            "registration_sha256": sha(PLAN),
            "marks": records,
        },
    )
    print(
        "admitted",
        sum(r["status"] == "VERIFIED" for r in records.values()),
        "of",
        len(records),
        flush=True,
    )


def account_folders(result):
    main = study.read(study.A04 / "result.json")["accounts"]
    for cost, account in result["long_accounts"].items():
        c = int(cost)
        native = study.read(BASELINES[c] / "independent-account-audit.json")
        yield f"original_{cost}bps", BASELINES[c], native["archive_sha256"], native["curve_sha256"]
        flat = main[f"FlatTopBoost_{cost}bps"]
        yield (
            f"flat_{cost}bps",
            FLAT_ROOT / f"FlatTopBoost_{cost}bps",
            flat["archive_sha256"],
            flat["curve_sha256"],
        )
        yield (
            f"solo_{cost}bps",
            study.OUT / f"long_{cost}bps",
            account["archive_sha256"],
            account["hourly_equity"]["sha256"],
        )


def read_accounts(result):
    accounts = {}
    for key, folder, archive_sha, curve_sha in account_folders(result):
        (archive,) = (folder / "native_results").glob("*.zip")
        checked({"path": str(archive), "sha256": archive_sha})
        curve = checked({"path": str(folder / "hourly_equity.parquet"), "sha256": curve_sha})
        with ZipFile(archive) as handle:
            (source,) = [
                n
                for n in handle.namelist()
                if n.endswith(".json") and not n.endswith("_config.json")
            ]
            (stats,) = json.loads(handle.read(source))["strategy"].values()
        accounts[key] = (stats["trades"], curve)
    return accounts


def known_market(plan, accounts, mark_files, oracle, hour, grid):
    source = Path(plan["datadir"])
    receipts = study.read(checked(plan["data_receipts"]))
    symbols = {t["pair"] for trades, _ in accounts.values() for t in trades}
    markets = {}
    for symbol in sorted(symbols):
        entry = mark_files[symbol]
        if entry["status"] != "VERIFIED":
            raise ValueError(f"No admissible mark prices for traded pair: {symbol}")
        mark5 = pd.read_parquet(checked(entry["frame"])).open.reindex(grid)
        funding = oracle.verified_frame(source, receipts, symbol, "1h-funding_rate")
        mark = oracle.verified_frame(source, receipts, symbol, "1h-mark")
        hour_mark = mark.set_index("date").open.reindex(hour)
        if not np.allclose(mark5.reindex(hour), hour_mark, rtol=1e-10, atol=1e-12):
            raise ValueError(f"Hourly mark archives disagree: {symbol}")
        markets[symbol] = (funding, mark, mark5.to_numpy(), hour_mark.to_numpy())
    return markets


def observe(plan):
    result = study.read(study.RESULT)
    accounts = read_accounts(result)
    data = study.read(OUT / "admission.json")
    if data["registration_sha256"] != sha(PLAN):
        raise ValueError("Marks were not registered")
    hour, grid = grids(plan)
    oracle = load_module("long_mark_oracle", mark_valuation.ORACLE)
    markets = known_market(plan, accounts, data["marks"], oracle, hour, grid)
    report = {}
    for key, (trades, curve) in accounts.items():
        hourly = np.full(len(hour), plan["initial_equity_usdt"], dtype=float)
        five = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in trades:
            funding, mark, v5, vh = markets[trade["pair"]]
            oracle.contribute(trade, hour.as_unit("ms").asi8, vh, funding, mark, hourly)
            oracle.contribute(trade, grid.as_unit("ms").asi8, v5, funding, mark, five)
        observed = pd.read_parquet(curve).set_index("date").hourly_mark_equity.reindex(hour)
        error = float(np.max(np.abs(hourly - observed.to_numpy())))
        if not np.isfinite(five).all() or not np.isfinite(error) or error > 1e-5:
            raise ValueError(f"Hourly parity or five-minute observations failed: {key}/{error}")
        dd = 1 - five / np.maximum.accumulate(five)
        path = OUT / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": five, "drawdown": dd}).to_parquet(path, index=False)
        report[key] = {
            "net_return": float(five[-1] / five[0] - 1),
            "drawdown_5m": float(dd.max()),
            "hourly_parity_error_usdt": error,
            "nav_points": len(five),
            "curve": record(path),
        }
    checks = {}
    for cost in (8, 15):
        original, flat, candidate = (report[f"{c}_{cost}bps"] for c in ("original", "flat", "solo"))
        checks[str(cost)] = {
            "within_1pp_risk": candidate["drawdown_5m"] - original["drawdown_5m"] <= 0.01,
            "better_return_drawdown": candidate["net_return"] * flat["drawdown_5m"]
            > flat["net_return"] * candidate["drawdown_5m"],
        }
    save(
        study.HERE / "long_mark_result.json",
        {
            "registration_sha256": sha(PLAN),
            "account_result_sha256": sha(study.RESULT),
            "admission_sha256": sha(OUT / "admission.json"),
            "accounts": report,
            "checks": checks,
            "paper_eligible": False,
        },
    )
    print(checks, flush=True)


def main():
    command = sys.argv[1]
    if command == "register":
        register()
        return
    policy = study.read(PLAN)
    for source in policy["sources"]:
        checked(source)
    {"acquire": acquire, "observe": observe}[command](study.read(study.LONG_PLAN))


if __name__ == "__main__":
    main()
