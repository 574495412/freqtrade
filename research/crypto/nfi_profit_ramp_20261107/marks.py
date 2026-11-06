"""Admit official marks, confirm hourly parity, and value 2024 ledgers."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study
from research.crypto.nfi_solo_allocation_20261107.long_mark import grids
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


OUT = study.OUT / "5m"


def acquire():
    plan = study.check()
    _, grid = grids(plan)
    OUT.mkdir(parents=True)
    mark_valuation.OUT = OUT

    def one(pair):
        try:
            _, series, source = mark_valuation.month_mark(pair, grid)
            path = OUT / f"{pair.split(':')[0].replace('/', '')}.parquet"
            series.rename("open").to_frame().to_parquet(path)
            return pair, {"status": "verified", **source, "frame": study.record(path)}
        except (OSError, ValueError) as error:
            return pair, {"status": "unknown", "reason": str(error)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        marks = dict(pool.map(one, plan["pairs"]))
    save(
        OUT / "admission.json",
        {
            "protocol_sha256": sha(study.PROTOCOL),
            "marks": marks,
        },
    )
    print(
        "Official five-minute mark tapes",
        sum(r["status"] == "verified" for r in marks.values()),
        "of",
        len(marks),
        flush=True,
    )


def revalue(plan, accounts):
    receipt = study.read(OUT / "admission.json")
    if receipt["protocol_sha256"] != sha(study.PROTOCOL):
        raise ValueError("Marks were not registered")
    hour, grid = grids(plan)
    temporal.OUT = study.OUT
    oracle = load_module("a13_mark_oracle", mark_valuation.ORACLE)
    holdings, sources, errors = mark_valuation.trades_and_checks(
        {"accounts": accounts}, plan, oracle, hour
    )
    if max(errors.values()) > 1e-5:
        raise ValueError("Hourly NAV failed independent parity")
    markets = {}
    for pair, (_, native_mark) in sources.items():
        row = receipt["marks"][pair]
        if row["status"] != "verified":
            raise ValueError(f"Official mark history missing for held contract {pair}")
        series = pd.read_parquet(checked(row["frame"])).open.reindex(grid)
        archived = native_mark.set_index("date").open.reindex(hour)
        if not np.isfinite(series).all() or not np.allclose(
            series.reindex(hour), archived, rtol=1e-10, atol=1e-12
        ):
            raise ValueError(f"Mark prices differ on common hourly clock: {pair}")
        markets[pair] = series.to_numpy()
    result = {}
    clocks = grid.as_unit("ms").asi8
    for name, trades in holdings.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in trades:
            symbol = trade["pair"]
            funding, mark = sources[symbol]
            oracle.contribute(trade, clocks, markets[symbol], funding, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError(f"Bad five-minute net value {name}")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = OUT / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        result[name] = {
            "drawdown_5m": float(dd.max()),
            "return_5m": float(nav[-1] / nav[0] - 1),
            "maximum_hourly_nav_error_usdt": errors[name],
            "points": len(nav),
            "curve": study.record(path),
        }
    return result


if __name__ == "__main__":
    acquire()
