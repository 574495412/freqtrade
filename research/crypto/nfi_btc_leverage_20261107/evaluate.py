"""Value all native ablations and apply the preregistered progression rules."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from research.crypto.nfi_btc_leverage_20261107 import study
from research.crypto.nfi_solo_allocation_20261107.long_mark import grids
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def value(plan, stage, accounts):
    hour, grid = grids(plan)
    temporal.OUT = study.OUT
    out = study.OUT / f"{stage}-mark"
    mark_valuation.OUT = out
    oracle = load_module("leverage_mark_oracle", mark_valuation.ORACLE)
    holdings, sources, errors = mark_valuation.trades_and_checks(
        {"accounts": accounts}, plan, oracle, hour
    )
    if max(errors.values()) > 1e-5:
        raise ValueError(f"Native hourly NAV reconciliation failed: {errors}")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=8) as pool:
            marked = list(pool.map(lambda pair: mark_valuation.month_mark(pair, grid), sources))
    else:
        records = study.read(study.a13.OUT / "5m/admission.json")["marks"]
        marked = [
            (pair, pd.read_parquet(checked(records[pair]["frame"])).open, records[pair])
            for pair in sources
        ]
    prices = {}
    for pair, price, _ in marked:
        frame = price.reindex(grid)
        native = sources[pair][1].set_index("date").open.reindex(hour)
        if (
            not np.isfinite(frame).all()
            or (frame <= 0).any()
            or not np.allclose(frame.reindex(hour), native, atol=1e-12, rtol=1e-10)
        ):
            raise ValueError(f"Mark grid does not reconcile for {pair}")
        prices[pair] = frame.to_numpy()

    out.mkdir(exist_ok=True)
    clocks = grid.as_unit("ms").asi8
    metrics = {}
    for key, ledger in holdings.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in ledger:
            pair = trade["pair"]
            fund, hourly = sources[pair]
            oracle.contribute(trade, clocks, prices[pair], fund, hourly, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError(f"Invalid mark equity for {key}")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = out / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        metrics[key] = {
            "drawdown": float(dd.max()),
            "points": len(nav),
            "hourly_error_usdt": errors[key],
            "curve": study.record(path),
        }
    return metrics, {pair: receipt for pair, _, receipt in marked}


def assess(stage, cases, accounts, risk):
    comparison = {}
    for cost in study.COSTS:
        key = f"nfi-x8-1x_{cost}bps"
        if stage == "temporal":
            original = study.read(study.A04 / "temporal_result.json")["accounts"][key]["periods"]
            dd0 = study.read(study.A04 / "mark_result.json")["accounts"][key]["drawdown_5m_mark"]
        else:
            parent = study.read(study.a13.RESULT)
            original = parent["accounts"][key]["periods"]
            dd0 = parent["mark_results"][key]["drawdown_5m"]
        base = original["continuous"]["net_return"]
        rows = {
            "Original1x": {
                "return": base,
                "drawdown": dd0,
                "period_returns": {p: r["net_return"] for p, r in original.items()},
                "natural_closes": original["continuous"]["completed_campaigns"],
            }
        }
        for name in cases:
            k = f"{stage}_{name}_{cost}bps"
            account = accounts[k]
            periods = account["periods"]
            full = periods["continuous"]
            net, dd = full["net_return"], risk[k]["drawdown"]
            rows[name] = {
                "return": net,
                "drawdown": dd,
                "period_returns": {p: r["net_return"] for p, r in periods.items()},
                "native_liquidations": account["native_liquidations"],
                "natural_closes": full["completed_campaigns"],
            }
            if study.CASES[name] == 2:
                rows[name]["checks"] = {
                    "no_liquidation": account["native_liquidations"] == 0,
                    "sample": full["completed_campaigns"] >= (12 if stage == "temporal" else 60),
                    "absolute_return": net > base + 0.01,
                    "risk_adjusted_return": net * dd0 >= base * dd,
                    "drawdown": dd <= 0.25,
                    "halves": stage == "temporal"
                    or all(
                        periods[h]["net_return"] > original[h]["net_return"] for h in ("H1", "H2")
                    ),
                }
        if "Direction2x" in rows:
            direction, native, control = (
                rows[n] for n in ("Direction2x", "Native2x", "Direction1x")
            )
            net, dd = direction["return"], direction["drawdown"]
            direction["checks"].update(
                direction_risk_adjusted=all(
                    net * r["drawdown"] > r["return"] * dd for r in (native, control)
                ),
                direction_return=net >= 0.8 * native["return"] and net > control["return"],
            )
        comparison[str(cost)] = rows
    return comparison


def passed(comparison, name):
    return all(all(rows[name]["checks"].values()) for rows in comparison.values())


def main():
    plans = study.check()
    study.OUT.mkdir()
    report = {"protocol_sha256": sha(study.PROTOCOL), "stages": {}, "paper_eligible": False}
    cases = tuple(study.CASES)
    advancing = ()
    for stage in ("temporal", "2024"):
        accounts = dict(
            study.account(plans[f"{stage}_{name}"], stage, name, cost)
            for cost in study.COSTS
            for name in cases
        )
        risk, sources = value(study.read(plans[f"{stage}_{cases[0]}"]), stage, accounts)
        comparison = assess(stage, cases, accounts, risk)
        eligible = advancing if stage == "2024" else ("Native2x", "Direction2x")
        advancing = tuple(name for name in eligible if passed(comparison, name))
        report["stages"][stage] = {
            "accounts": accounts,
            "marked_risk": risk,
            "mark_sources": sources,
            "comparison": comparison,
            "passing_2x_cases": advancing,
        }
        save(study.RESULT, report)
        print(stage, comparison, flush=True)
        if not advancing:
            break
        cases = tuple(study.CASES) if "Direction2x" in advancing else ("Native2x",)


if __name__ == "__main__":
    main()
