"""Reconstruct marked accounts and apply the frozen exit-deferral gates."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_tail_20261107 import study
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def marks(plan, stage, accounts):
    hour, grid = long_mark.grids(plan)
    temporal.OUT = study.OUT
    out = study.OUT / f"{stage}-mark"
    mark_valuation.OUT = out
    oracle = load_module("profit_tail_mark_oracle", mark_valuation.ORACLE)
    trades, sources, errors = mark_valuation.trades_and_checks(
        {"accounts": accounts}, plan, oracle, hour
    )
    if max(errors.values()) > 1e-5:
        raise ValueError("Independent native hourly mark reconciliation failed")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=8) as pool:
            data = list(pool.map(lambda p: mark_valuation.month_mark(p, grid), sources))
    else:
        parents = study.read(long_mark.OUT / "admission.json")["marks"]
        data = [
            (s, pd.read_parquet(checked(parents[s]["frame"])).open, parents[s]) for s in sources
        ]
    prices = {s: x.reindex(grid) for s, x, _ in data}
    for s, frame in prices.items():
        hourly = sources[s][1].set_index("date").open.reindex(hour)
        if not np.isfinite(frame).all() or not np.allclose(
            frame.reindex(hour), hourly, atol=1e-12, rtol=1e-10
        ):
            raise ValueError(f"Native and public mark grids disagree: {s}")
    out.mkdir(exist_ok=True)
    clocks = grid.as_unit("ms").asi8
    output = {}
    for key, ledger in trades.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for t in ledger:
            pair = t["pair"]
            funding, mark = sources[pair]
            oracle.contribute(t, clocks, prices[pair].to_numpy(), funding, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Invalid mark equity")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = out / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        output[key] = {
            "drawdown": float(dd.max()),
            "points": len(nav),
            "hourly_error_usdt": errors[key],
            "curve": study.record(path),
        }
    return output, {s: receipt for s, _, receipt in data}


def assess(stage, accounts, risk):
    out = {}
    for cost in study.COSTS:
        key = f"nfi-x8-1x_{cost}bps"
        if stage == "temporal":
            source = study.read(study.A04 / "temporal_result.json")["accounts"][key]["periods"]
            original = {k: v["net_return"] for k, v in source.items()}
            dd0 = study.read(study.A04 / "mark_result.json")["accounts"][key]["drawdown_5m_mark"]
        else:
            source = study.read(study.A04 / "result.json")["accounts"][f"FlatTopBoost_{cost}bps"][
                "periods"
            ]
            original = {k: v["baseline_return"] for k, v in source.items()}
            dd0 = study.read(long_mark.study.HERE / "long_mark_result.json")["accounts"][
                f"original_{cost}bps"
            ]["drawdown_5m"]
        f_key, t_key = (f"{stage}_{s}_{cost}bps" for s in study.CASES)
        f, t = (accounts[s]["periods"] for s in (f_key, t_key))
        net0, net1, net2 = (
            original["continuous"],
            f["continuous"]["net_return"],
            t["continuous"]["net_return"],
        )
        dd1, dd2 = (risk[s]["drawdown"] for s in (f_key, t_key))
        closes, deferred = (20, 4) if stage == "temporal" else (60, 20)
        out[str(cost)] = {
            "original_return": net0,
            "original_drawdown": dd0,
            "flat_return": net1,
            "flat_drawdown": dd1,
            "trend_return": net2,
            "trend_drawdown": dd2,
            "checks": {
                "returns": net2 > max(0, net0, net1),
                "risk_adjusted_returns": net2 * dd0 > net0 * dd2 and net2 * dd1 > net1 * dd2,
                "risk_budget": dd2 <= dd0 + 0.01,
                "samples": t["continuous"]["completed_campaigns"] >= closes
                and accounts[t_key]["naturally_closed_deferred"] >= deferred,
                "slices": stage == "temporal"
                or all(v["net_return"] > original[k] for k, v in t.items()),
            },
        }
    return out


def main():
    plans = study.check()
    study.OUT.mkdir()
    report = {"protocol_sha256": sha(study.PROTOCOL), "stages": {}, "paper_eligible": False}
    for stage in ("temporal", "long"):
        path = plans[stage]
        accounts = dict(study.account(path, stage, s, c) for c in study.COSTS for s in study.CASES)
        risk, receipts = marks(study.read(path), stage, accounts)
        result = assess(stage, accounts, risk)
        passed = all(all(v["checks"].values()) for v in result.values())
        report["stages"][stage] = {
            "accounts": accounts,
            "marked_risk": risk,
            "mark_sources": receipts,
            "comparison": result,
            "passed": passed,
        }
        save(study.RESULT, report)
        print(stage, result, flush=True)
        if not passed:
            break


if __name__ == "__main__":
    main()
