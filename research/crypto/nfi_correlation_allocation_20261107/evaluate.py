"""Verify marked risk and compare each stage with the sealed native controls."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from research.crypto.nfi_correlation_allocation_20261107 import study
from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def value_5m(plan, stage, accounts):
    out = study.OUT / f"{stage}-mark"
    temporal.OUT = study.OUT
    mark_valuation.OUT = out
    hour, grid = long_mark.grids(plan)
    oracle = load_module("a14_mark_oracle", mark_valuation.ORACLE)
    books = {f"{stage}_{cost}bps": row for cost, row in accounts.items()}
    holdings, sources, parity = mark_valuation.trades_and_checks(
        {"accounts": books}, plan, oracle, hour
    )
    if max(parity.values()) > 1e-5:
        raise ValueError("Independent hourly reconstruction differs")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=6) as pool:
            marked = list(pool.map(lambda s: mark_valuation.month_mark(s, grid), sources))
    else:
        parent = a13.OUT / "5m" if stage == "2024" else long_mark.OUT
        rows = study.read(parent / "admission.json")["marks"]
        marked = [(s, pd.read_parquet(checked(rows[s]["frame"])).open, rows[s]) for s in sources]
    prices = {s: v.reindex(grid) for s, v, _ in marked}
    for s, data in prices.items():
        hourly = sources[s][1].set_index("date").open.reindex(hour)
        if (
            not np.isfinite(data).all()
            or (data <= 0).any()
            or not np.allclose(data.reindex(hour), hourly, rtol=1e-10, atol=1e-12)
        ):
            raise ValueError(f"Official five-minute marks differ: {s}")
    out.mkdir(exist_ok=True)
    clocks = grid.as_unit("ms").asi8
    results = {}
    for key, trades in holdings.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for t in trades:
            s = t["pair"]
            fund, mark = sources[s]
            oracle.contribute(t, clocks, prices[s].to_numpy(), fund, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Invalid five-minute account")
        dd = 1 - nav / np.maximum.accumulate(nav)
        dest = out / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(dest, index=False)
        results[key] = {
            "drawdown": float(dd.max()),
            "net_return": float(nav[-1] / nav[0] - 1),
            "hourly_error_usdt": parity[key],
            "points": len(nav),
            "curve": study.record(dest),
        }
    return results, {s: r for s, _, r in marked}


def controls(stage, cost):
    original, fixed = (f"{s}_{cost}bps" for s in (a13.ORIGINAL, a13.FIXED))
    if stage == "2024":
        r = study.read(a13.RESULT)
        returns = {
            n: {k: v["net_return"] for k, v in r["accounts"][n]["periods"].items()}
            for n in (original, fixed)
        }
        risk = {n: r["mark_results"][n]["drawdown_5m"] for n in returns}
    elif stage == "temporal":
        r = study.read(study.A04 / "temporal_result.json")["accounts"]
        marks = study.read(study.A04 / "mark_result.json")["accounts"]
        returns = {
            n: {k: v["net_return"] for k, v in r[n]["periods"].items()} for n in (original, fixed)
        }
        risk = {n: marks[n]["drawdown_5m_mark"] for n in returns}
    else:
        r = study.read(study.A04 / "result.json")["accounts"][fixed]["periods"]
        marks = study.read(long_mark.study.HERE / "long_mark_result.json")["accounts"]
        returns = {
            original: {k: v["baseline_return"] for k, v in r.items()},
            fixed: {k: v["net_return"] for k, v in r.items()},
        }
        risk = {
            original: marks[f"original_{cost}bps"]["drawdown_5m"],
            fixed: marks[f"flat_{cost}bps"]["drawdown_5m"],
        }
    return (returns[original], returns[fixed], risk[original], risk[fixed])


def compare(stage, accounts, marked):
    result = {}
    for cost, a in accounts.items():
        orig, fixed, orig_dd, flat_dd = controls(stage, cost)
        periods = a["periods"]
        net = periods["continuous"]["net_return"]
        dd = marked[f"{stage}_{cost}bps"]["drawdown"]
        minimum = (20, 10, 5) if stage == "temporal" else (60, 20, 10)
        checks = {
            "above_original_in_all_periods": net > 0
            and all(p["net_return"] > orig[k] for k, p in periods.items()),
            "within_one_pp_risk": dd - orig_dd <= 0.01,
            "above_fixed_on_return_drawdown": net * flat_dd > fixed["continuous"] * dd,
            "sample": periods["continuous"]["completed_campaigns"] >= minimum[0]
            and a["scoped_fills"] >= minimum[1]
            and a["boosted_fills"] >= minimum[2],
        }
        result[str(cost)] = {
            "checks": checks,
            "original": {"period_returns": orig, "drawdown": orig_dd},
            "fixed": {"period_returns": fixed, "drawdown": flat_dd},
            "candidate": {
                "period_returns": {k: v["net_return"] for k, v in periods.items()},
                "drawdown": dd,
            },
        }
    return result


def main():
    plans = study.check()
    study.OUT.mkdir()
    stages = {}
    for stage in ("2024", "temporal", "long"):
        path = plans[stage]
        accounts = {str(cost): study.run_account(stage, path, cost) for cost in study.COSTS}
        marks, mark_sources = value_5m(study.read(path), stage, accounts)
        compared = compare(stage, accounts, marks)
        passed = all(all(r["checks"].values()) for r in compared.values())
        stages[stage] = {
            "accounts": accounts,
            "mark_audit": marks,
            "mark_sources": mark_sources,
            "comparison": compared,
            "passed": passed,
        }
        save(
            study.RESULT,
            {
                "protocol_sha256": sha(study.PROTOCOL),
                "stages": stages,
                "passed_all_stages": len(stages) == 3 and passed,
                "paper_eligible": False,
            },
        )
        print(stage, compared, flush=True)
        if not passed:
            break


if __name__ == "__main__":
    main()
