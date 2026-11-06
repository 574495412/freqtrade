"""Run staged native comparisons and verify risk with official five-minute marks."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from research.crypto.nfi_correlation_allocation_20261107.evaluate import controls
from research.crypto.nfi_residual_recovery_20261107 import study
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def revalue(plan, stage, accounts):
    hour, grid = long_mark.grids(plan)
    oracle = load_module("a15_oracle", mark_valuation.ORACLE)
    books = {f"{stage}_{cost}bps": a for cost, a in accounts.items()}
    temporal.OUT = study.OUT
    folder = study.OUT / f"{stage}-mark"
    mark_valuation.OUT = folder
    trades, source, parity = mark_valuation.trades_and_checks(
        {"accounts": books}, plan, oracle, hour
    )
    if max(parity.values()) > 1e-5:
        raise ValueError("Native hourly equity did not independently reconcile")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=6) as pool:
            raw = list(pool.map(lambda s: mark_valuation.month_mark(s, grid), source))
    else:
        admitted = study.read(long_mark.OUT / "admission.json")["marks"]
        raw = [
            (s, pd.read_parquet(checked(admitted[s]["frame"])).open, admitted[s]) for s in source
        ]
    prices = {s: x.reindex(grid) for s, x, _ in raw}
    for s, series in prices.items():
        original = source[s][1].set_index("date").open.reindex(hour)
        if not np.isfinite(series).all() or not np.allclose(
            series.reindex(hour), original, rtol=1e-10, atol=1e-12
        ):
            raise ValueError(f"Official mark calendar differs: {s}")
    folder.mkdir(exist_ok=True)
    results = {}
    clock = grid.as_unit("ms").asi8
    for key, ledger in trades.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in ledger:
            s = trade["pair"]
            funding, mark = source[s]
            oracle.contribute(trade, clock, prices[s].to_numpy(), funding, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Invalid five-minute equity")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = folder / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        results[key] = {
            "drawdown": float(dd.max()),
            "net_return": float(nav[-1] / nav[0] - 1),
            "hourly_error_usdt": parity[key],
            "points": len(nav),
            "curve": study.record(path),
        }
    return results, {s: row for s, _, row in raw}


def compare(stage, accounts, risk):
    out = {}
    for cost, account in accounts.items():
        orig, flat, dd0, dd1 = controls(stage, cost)
        periods = account["periods"]
        full = periods["continuous"]
        net, dd = full["net_return"], risk[f"{stage}_{cost}bps"]["drawdown"]
        minimum = (20, 10, 4) if stage == "temporal" else (60, 20, 10)
        out[cost] = {
            "original_return": orig["continuous"],
            "original_drawdown": dd0,
            "fixed_return": flat["continuous"],
            "fixed_drawdown": dd1,
            "candidate_return": net,
            "candidate_drawdown": dd,
            "checks": {
                "beat_original": net > max(0, orig["continuous"]),
                "within_one_pp_drawdown": dd - dd0 <= 0.01,
                "beat_fixed_return_drawdown": net * dd1 > flat["continuous"] * dd,
                "samples": full["completed_campaigns"] >= minimum[0]
                and account["scoped_fills"] >= minimum[1]
                and account["boosted_fills"] >= minimum[2],
                "long_slices": stage == "temporal"
                or all(v["net_return"] > orig[k] for k, v in periods.items()),
            },
        }
    return out


def main():
    policy = study.checked_protocol()
    study.OUT.mkdir()
    diag = study.attribute(policy)
    save(study.OUT / "attribution.json", diag)
    diagnostic_pass = all(v["passed"] for k, v in diag.items() if not k.startswith("temporal_"))
    print("Original path attribution", {k: v["groups"] for k, v in diag.items()}, flush=True)
    report = {
        "protocol_sha256": sha(study.PROTOCOL),
        "original_attribution": diag,
        "diagnostic_gate_passed": diagnostic_pass,
        "stages": {},
        "paper_eligible": False,
    }
    for stage in ("temporal", "long"):
        plan_path = checked(policy["runs"][stage])
        accounts = {str(c): study.run_account(plan_path, stage, c) for c in study.COSTS}
        risk, mark_sources = revalue(study.read(plan_path), stage, accounts)
        comparison = compare(stage, accounts, risk)
        admitted = all(all(r["checks"].values()) for r in comparison.values())
        report["stages"][stage] = {
            "accounts": accounts,
            "risk": risk,
            "mark_sources": mark_sources,
            "comparison": comparison,
            "passed": admitted,
        }
        save(study.RESULT, report)
        print(stage, comparison, flush=True)
        if not admitted or not diagnostic_pass:
            break


if __name__ == "__main__":
    main()
