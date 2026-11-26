"""Report verified annual native account results with their shared cost and data contract."""

import argparse
from math import isclose

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .accounts import COSTS
from .inputs import HERE
from .low_disk_run import BLOCK_RESEARCH, RESEARCH


ROLES = ("original", "fixed_boost_12", "boost_14")


def result(folder, cost, role, year=2025):
    completion = folder / "completion.json"
    summary_path = folder / "account_reconciled.json"
    if not completion.exists() or not summary_path.exists() or read(completion)["returncode"] != 0:
        return None
    summary = read(summary_path)
    output = folder / "account"
    plan_path = output / "registered_plan.json"
    plan = read(plan_path)
    case, = plan["candidates"]
    assert case["name"] == role
    start, end = map(pd.Timestamp, (plan["start"], plan["end"]))
    assert start == pd.Timestamp(f"{year}-01-01", tz="UTC")
    assert end == start + pd.DateOffset(years=1)
    days = (end - start).days
    assert summary["five_minute_nav_reconciliation"]["passed"]
    assert summary["pool"]["decision_days_checked"] == days
    assert read(output / "result.json")["cost_bps_per_side"] == cost
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="5min")
    assert len(grid) == days * 288
    sources = {checked(row).suffix: row for row in summary["sources"]}
    wallet = pd.read_parquet(checked(sources[".parquet"]))
    assert pd.DatetimeIndex(wallet.date).as_unit("ns").equals(grid.as_unit("ns"))
    assert wallet.marked_equity.iloc[0] == plan["initial_equity_usdt"] == 1000
    ratio = wallet.marked_equity / wallet.marked_equity.iloc[0]
    performance = summary["periods"]["continuous"]
    assert isclose(performance["net_return"], float(ratio.iloc[-1] - 1), abs_tol=1e-12)
    assert isclose(performance["drawdown"], float((1 - ratio / ratio.cummax()).max()), abs_tol=1e-12)
    trades = stats_from(checked(sources[".zip"]))["trades"]
    natural = [row["profit_abs"] for row in trades if row["exit_reason"] != "force_exit"]
    return {
        "net_return": performance["net_return"], "drawdown": performance["drawdown"],
        "final_equity_usdt": performance["ending_equity"], "mark_points": len(grid),
        "peak_positions": int(wallet.open_trades.max()), "trades": len(trades),
        "natural_closed_trades": len(natural),
        "natural_profit_at_least_25_usdt": sum(value >= 25 for value in natural),
        "natural_profit_at_least_50_usdt": sum(value >= 50 for value in natural),
        "largest_natural_winner_usdt": max((value for value in natural if value > 0), default=None),
        "largest_natural_loss_usdt": min((value for value in natural if value < 0), default=None),
        "top_five_profit_fraction": summary["profits"]["top_five_fraction_of_gross_gains"],
        "receipts": [receipt(summary_path), receipt(completion), receipt(plan_path),
                     *summary["sources"], receipt(output / "bounded_storage_audit.json")],
    }, {name: plan[name] for name in ("start", "end", "historical_schedule", "data_receipts",
                                     "market_snapshot", "initial_equity_usdt", "allowed_leverage",
                                     "engine_commit", "runner_sha256", "observer_source")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("frames", "blocks"), default="blocks")
    parser.add_argument("--year", type=int, choices=(2024, 2025), default=2025)
    args = parser.parse_args()
    prefix = "annual" if args.year == 2025 else f"annual_{args.year}"
    research = BLOCK_RESEARCH if args.backend == "blocks" else RESEARCH
    series = {}
    contract = None
    completed = 0
    for cost in COSTS:
        roles = {}
        for role in ROLES:
            folder = research / f"{prefix}_{role}_{cost}bp"
            row = result(folder, cost, role, args.year)
            if row is None:
                continue
            roles[role], used = row
            if contract is None:
                contract = used
            assert contract == used
            completed += 1
        series[str(cost)] = roles
    all_ready = completed == len(COSTS) * len(ROLES)
    suffix = "matched_comparison" if all_ready else "verified_progress"
    save(HERE / f"{prefix}_{suffix}.json", {
        "contract": contract, "completed_native_accounts": completed, "cohort_complete": all_ready,
        "accounts_by_cost_bp": series, "matched_scope": "Original and both fixed-boost variants",
        "orders_enabled": False,
    })
    for cost, roles in series.items():
        print(cost, {role: (row["net_return"], row["drawdown"], row["trades"])
                     for role, row in roles.items()})


if __name__ == "__main__":
    main()
