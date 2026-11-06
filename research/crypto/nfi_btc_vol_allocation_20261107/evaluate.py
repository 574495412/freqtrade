"""Apply the registered matched-account gates using official five-minute marks."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_btc_vol_allocation_20261107 import study
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def revalue(plan_path, stage, accounts):
    plan = study.read(plan_path)
    hour, grid = long_mark.grids(plan)
    oracle = load_module("a09_valuation_oracle", mark_valuation.ORACLE)
    temporal.OUT = study.OUT
    mark_valuation.OUT = study.OUT / f"{stage}_mark"
    books = {f"{stage}_{cost}bps": row for cost, row in accounts.items()}
    holdings, sources, hourly_errors = mark_valuation.trades_and_checks(
        {"accounts": books}, plan, oracle, hour
    )
    if max(hourly_errors.values()) > 1e-5:
        raise ValueError("Hourly NAV failed independent reconciliation")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=8) as pool:
            marked = list(pool.map(lambda s: mark_valuation.month_mark(s, grid), sources))
    else:
        admission = study.read(long_mark.OUT / "admission.json")
        if admission["registration_sha256"] != sha(long_mark.PLAN):
            raise ValueError("Long-window mark admission differs")
        marked = []
        for pair in sources:
            entry = admission["marks"][pair]
            if entry["status"] != "VERIFIED":
                raise ValueError(f"Unverified five-minute marks: {pair}")
            series = pd.read_parquet(checked(entry["frame"])).open
            marked.append((pair, series, entry))
    marks = {pair: series.reindex(grid) for pair, series, _ in marked}
    for pair, series in marks.items():
        archived = sources[pair][1].set_index("date").open.reindex(hour)
        if not np.isfinite(series).all() or not np.allclose(
            series.reindex(hour), archived, rtol=1e-10, atol=1e-12
        ):
            raise ValueError(f"Mark grid did not reconcile: {pair}")
    mark_valuation.OUT.mkdir(exist_ok=True)
    risk = {}
    for key, trades in holdings.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for trade in trades:
            pair = trade["pair"]
            funding, mark = sources[pair]
            oracle.contribute(
                trade, grid.as_unit("ms").asi8, marks[pair].to_numpy(), funding, mark, nav
            )
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Invalid five-minute NAV")
        dd = 1 - nav / np.maximum.accumulate(nav)
        path = mark_valuation.OUT / f"{key}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(path, index=False)
        risk[key] = {
            "drawdown_5m": float(dd.max()),
            "net_return_5m": float(nav[-1] / nav[0] - 1),
            "hourly_error_usdt": hourly_errors[key],
            "curve": study.record(path),
            "points": len(nav),
        }
    return risk, {pair: info for pair, _, info in marked}


def evaluate(stage, accounts, marks):
    a08 = study.read(study.previous.RESULT)
    if stage == "temporal":
        returns = study.read(study.A04 / "temporal_result.json")["accounts"]
        risk = study.read(study.A04 / "mark_result.json")["accounts"]
    else:
        returns = study.read(study.A04 / "result.json")["accounts"]
        risk = study.read(study.previous.HERE / "long_mark_result.json")["accounts"]
    result = {}
    for cost, account in accounts.items():
        fixed_key, original_key = f"FlatTopBoost_{cost}bps", f"nfi-x8-1x_{cost}bps"
        full = account["periods"]["continuous"]
        if stage == "temporal":
            baseline = {
                key: {
                    "net_return": returns[n]["periods"]["continuous"]["net_return"],
                    "drawdown_5m": risk[n]["drawdown_5m_mark"],
                }
                for key, n in (("original", original_key), ("flat", fixed_key))
            }
            baseline["solo"] = {
                "net_return": a08["temporal_accounts"][cost]["periods"]["continuous"]["net_return"],
                "drawdown_5m": a08["mark_audits"][f"temporal_{cost}bps"]["drawdown"],
            }
            extra = {
                "sample": full["completed_campaigns"] >= 20
                and account["matched_filled_top_coin_campaigns"] >= 10
                and account["filled_multipliers"].get("1.75", 0) > 0
            }
        else:
            baseline = {
                key: {k: risk[f"{key}_{cost}bps"][k] for k in ("net_return", "drawdown_5m")}
                for key in ("original", "flat", "solo")
            }
            extra = {
                "all_periods_beat_original": all(
                    row["net_return"] > returns[fixed_key]["periods"][key]["baseline_return"]
                    for key, row in account["periods"].items()
                )
            }
        net = full["net_return"]
        dd = marks[f"{stage}_{cost}bps"]["drawdown_5m"]
        checks = {
            "beats_original": net > max(0, baseline["original"]["net_return"]),
            "within_1pp_risk": dd - baseline["original"]["drawdown_5m"] <= 0.01,
            "beats_fixed_on_return_drawdown": net * baseline["flat"]["drawdown_5m"]
            > baseline["flat"]["net_return"] * dd,
            "beats_solo_on_return_drawdown": net * baseline["solo"]["drawdown_5m"]
            > baseline["solo"]["net_return"] * dd,
            **extra,
        }
        result[cost] = {
            "candidate": {"net_return": net, "drawdown_5m": dd},
            "controls": baseline,
            "checks": checks,
        }
    return result


def main():
    for source in study.read(study.REGISTRATION)["sources"]:
        checked(source)
    temporal_accounts = study.read(study.OUT / "temporal_audit.json")
    marks, receipts = revalue(study.SHORT_PLAN, "temporal", temporal_accounts)
    matched = evaluate("temporal", temporal_accounts, marks)
    report = {
        "protocol_sha256": sha(study.REGISTRATION),
        "evaluator_sha256": sha(Path(__file__)),
        "temporal_accounts": temporal_accounts,
        "temporal_marks": marks,
        "temporal_mark_receipts": receipts,
        "temporal_matched": matched,
        "long_admitted": all(all(row["checks"].values()) for row in matched.values()),
        "paper_eligible": False,
    }
    save(study.RESULT, report)
    print(matched, flush=True)
    if report["long_admitted"]:
        accounts = {str(cost): study.account(study.LONG_PLAN, "long", cost) for cost in (15, 8)}
        marks, receipts = revalue(study.LONG_PLAN, "long", accounts)
        report.update(
            long_accounts=accounts,
            long_marks=marks,
            long_mark_receipts=receipts,
            long_matched=evaluate("long", accounts, marks),
        )
        save(study.RESULT, report)


if __name__ == "__main__":
    main()
