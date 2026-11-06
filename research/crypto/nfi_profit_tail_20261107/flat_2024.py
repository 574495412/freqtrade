"""Revalidate the unchanged A18 flat control on a separate 2024 calendar."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_profit_tail_20261107 import study
from research.crypto.nfi_solo_allocation_20261107.long_mark import grids
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
PLAN = HERE / "flat_native_2024.json"
PROTOCOL = HERE / "flat_protocol_2024.json"
RESULT = HERE / "flat_result_2024.json"
STAGE = "followup-2024"
NAME = "FlatProfitTail"
PARENT_MARKS = a13.OUT / "5m/admission.json"


def register():
    if PROTOCOL.exists():
        raise FileExistsError("2024 flat-tail follow-up already registered")
    temporal_plan = study.check()["temporal"]
    flat = next(c for c in study.read(temporal_plan)["candidates"] if c["name"] == NAME)
    native = study.read(a13.PLAN)
    baseline = next(c for c in native["candidates"] if c["name"] == a13.ORIGINAL)
    cfg = study.read(checked(flat["config"]))
    baseline_cfg = study.read(checked(baseline["config"]))
    if {**cfg, "strategy": baseline_cfg["strategy"]} != baseline_cfg:
        raise ValueError("Follow-up differs from the fixed 2024 native account")
    save(
        PLAN,
        {
            **native,
            "schema": "freqtrade.research.nfi-flat-profit-tail-2024.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "candidates": [flat],
        },
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [
                study.record(p)
                for p in (
                    Path(__file__),
                    PLAN,
                    study.RESULT,
                    study.PROTOCOL,
                    a13.PLAN,
                    a13.RESULT,
                    PARENT_MARKS,
                    mark_valuation.ORACLE,
                    Path(mark_valuation.__file__),
                    Path(temporal.__file__),
                )
            ],
            "rule": (
                "Freeze the existing unconditional A18 control unchanged. 65-day "
                "economics were known and motivated this follow-up. Calendar 2024 "
                "account comparisons use known original/flat-stake controls, "
                "with new flat-tail economics unavailable at registration."
            ),
            "gate": (
                "Both costs must beat original on positive total and both half-window "
                "returns and full return/5m drawdown, keep 5m drawdown <= original +1pp, "
                "and have at least 60 natural closes including 20 deferred natural "
                "closes. Fixed 1.25x stake economics are reported separately."
            ),
            "orders_enabled": False,
        },
    )


def marks(plan, accounts):
    hour, grid = grids(plan)
    oracle = load_module("flat_2024_mark_oracle", mark_valuation.ORACLE)
    temporal.OUT = study.OUT
    ledgers, markets, errors = mark_valuation.trades_and_checks(
        {"accounts": accounts}, plan, oracle, hour
    )
    if max(errors.values()) > 1e-5:
        raise ValueError("Hourly NAV did not reconcile")
    source = study.read(PARENT_MARKS)["marks"]
    prices = {}
    for pair, (_, native_mark) in markets.items():
        tape = pd.read_parquet(checked(source[pair]["frame"])).open.reindex(grid)
        hourly = native_mark.set_index("date").open.reindex(hour)
        if not np.isfinite(tape).all() or not np.allclose(
            tape.reindex(hour), hourly, atol=1e-12, rtol=1e-10
        ):
            raise ValueError(f"Official five-minute marks disagree: {pair}")
        prices[pair] = tape.to_numpy()
    clock = grid.as_unit("ms").asi8
    out = study.OUT / "followup-2024-mark"
    out.mkdir()
    reports = {}
    for name, trades in ledgers.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for t in trades:
            symbol = t["pair"]
            funding, mark = markets[symbol]
            oracle.contribute(t, clock, prices[symbol], funding, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Invalid equity")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = out / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        reports[name] = {
            "drawdown": float(dd.max()),
            "points": len(nav),
            "hourly_error_usdt": errors[name],
            "curve": study.record(path),
        }
    return reports


def run():
    for ref in study.read(PROTOCOL)["sources"]:
        checked(ref)
    study.check()
    accounts = dict(study.account(PLAN, STAGE, NAME, cost) for cost in study.COSTS)
    risk = marks(study.read(PLAN), accounts)
    control = study.read(a13.RESULT)
    comparison = {}
    for cost in study.COSTS:
        original, flat = (f"{s}_{cost}bps" for s in (a13.ORIGINAL, a13.FIXED))
        key = f"{STAGE}_{NAME}_{cost}bps"
        base, fixed = (control["accounts"][n]["periods"] for n in (original, flat))
        candidate = accounts[key]["periods"]
        dd = risk[key]["drawdown"]
        original_dd, flat_dd = (control["mark_results"][n]["drawdown_5m"] for n in (original, flat))
        net = candidate["continuous"]["net_return"]
        comparison[str(cost)] = {
            "original_return": base["continuous"]["net_return"],
            "original_drawdown": original_dd,
            "flat_stake_return": fixed["continuous"]["net_return"],
            "flat_stake_drawdown": flat_dd,
            "flat_tail_return": net,
            "flat_tail_drawdown": dd,
            "checks": {
                "net_returns": net > 0
                and all(v["net_return"] > base[k]["net_return"] for k, v in candidate.items()),
                "risk_adjusted_return": net * original_dd > base["continuous"]["net_return"] * dd,
                "risk": dd <= original_dd + 0.01,
                "sample": candidate["continuous"]["completed_campaigns"] >= 60
                and accounts[key]["naturally_closed_deferred"] >= 20,
            },
        }
    save(
        RESULT,
        {
            "registration_sha256": sha(PROTOCOL),
            "accounts": accounts,
            "marks": risk,
            "comparison": comparison,
            "passed": all(all(row["checks"].values()) for row in comparison.values()),
            "paper_eligible": False,
        },
    )
    print(comparison, flush=True)


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
