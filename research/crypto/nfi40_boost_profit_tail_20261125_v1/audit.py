"""Independently reconcile this native account and both frozen controls."""

import argparse
from collections import Counter
from math import fsum

import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import (
    metrics, periods_for,
)
from research.crypto.nfi40_eight_slots_20261125_v1.audit import (
    compare_trade_counts, independent_nav, summarize_campaigns,
)
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi_profit_tail_20261107.study import audit as audit_tail
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked, save,
)

from .run import HERE, NAME, OUTPUT, read, receipt


def profit_profile(stats, cutoff):
    opened, closed, additions = summarize_campaigns(stats, cutoff)
    payouts = sorted((trade["profit_abs"] for trade in closed.values()), reverse=True)
    winners = [amount for amount in payouts if amount > 0]
    gross_gains = fsum(winners)
    return {
        "independent_entries": len(opened),
        "natural_closes": len(closed),
        "adds": additions,
        "largest_winner_usdt": max(winners) if winners else None,
        "largest_loss_usdt": min(payouts) if payouts and payouts[-1] < 0 else None,
        "top_five_profit_usdt": fsum(winners[:5]),
        "top_five_fraction_of_gross_gains": fsum(winners[:5]) / gross_gains if winners else None,
        "exit_reasons": dict(Counter(trade["exit_reason"] for trade in closed.values())),
    }


def analyze(plan, result, archive, curve, cost):
    stats = stats_from(archive)
    assert all(row["leverage"] == 1 and row["fee_open"] == row["fee_close"] == cost / 10000
               for row in stats["trades"])
    frame = pd.read_parquet(curve).set_index("date")
    continuous = plan["periods"][0]
    grid = pd.date_range(continuous["start"], continuous["end"], freq="h").as_unit("ns")
    assert frame.index.as_unit("ns").equals(grid)
    frame.index = grid
    if len(plan["periods"]) == 1:
        periods = [{"name": continuous["name"], **metrics(frame.hourly_mark_equity)}]
    else:
        periods = periods_for(frame.hourly_mark_equity, plan["periods"])
    assert abs(periods[0]["net_return"] - read(result)["periods"][0]["net_return"]) < 1e-12
    cutoff = int(grid[-1].timestamp() * 1000)
    summary = {
        "periods": periods,
        "profits": profit_profile(stats, cutoff),
        "reconciliation": independent_nav(plan, stats["trades"], grid, frame),
        "source_receipts": [receipt(path) for path in (result, archive, curve)],
    }
    return stats, summary


def compare(cost):
    plan = read(HERE / "plan.json")
    checked(plan["protocol"])
    run_path = OUTPUT / f"cost_{cost}bp"
    assert read(run_path / "registered_plan.json") == plan
    assert read(run_path / "fill_cash_audit.json")["passed"] is True
    fixed_result, fixed_archive, fixed_curve, fixed_config = map(
        checked, plan["controls"][str(cost)]
    )
    config = read(run_path / "effective_config.json")
    fixed = read(fixed_config)
    assert config["strategy"] == NAME and fixed["strategy"] == "FlatTopBoost"
    for key in (
        "timeframe", "dry_run_wallet", "stake_amount", "tradable_balance_ratio",
        "trading_mode", "margin_mode", "order_types", "fee", "timerange",
        "max_open_trades", "datadir", "pairlists", "exchange",
    ):
        assert config[key] == fixed[key], key
    for name, row in plan["candidates"][0]["source_files"].items():
        checked({"path": run_path / "strategies" / name, "sha256": row["sha256"]})
    original = next(
        row for row in read(HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json")[
            "baselines"
        ] if row["cost_bps_per_side"] == cost
    )
    original_outputs = tuple(map(checked, original["old_native_outputs_rechecked"]))
    archive, = (run_path / "native_results").glob("*.zip")
    new_stats, candidate = analyze(plan, run_path / "result.json", archive,
                                   run_path / "hourly_equity.parquet", cost)
    boost_stats, boost = analyze(plan, fixed_result, fixed_archive, fixed_curve, cost)
    _, reference = analyze(plan, *original_outputs, cost)
    cutoff = int(pd.Timestamp(plan["periods"][0]["end"]).timestamp() * 1000)
    native_logs = audit_tail(plan, run_path, NAME, new_stats["trades"])
    candidate["tail_callbacks"] = native_logs
    counts = compare_trade_counts(boost_stats, new_stats, cutoff)
    return_lift = candidate["periods"][0]["net_return"] - boost["periods"][0]["net_return"]
    drawdown_change = (candidate["periods"][0]["hourly_mark_drawdown"]
                       - boost["periods"][0]["hourly_mark_drawdown"])
    result = {
        "cost_bps_per_side": cost,
        "original": reference, "fixed_boost": boost, "candidate": candidate,
        "return_lift_over_boost": return_lift,
        "drawdown_change_over_boost": drawdown_change,
        "count_comparison_to_boost": counts,
        "advance": return_lift > 0 and all(
            row["reconciliation"]["passed"] for row in (candidate, boost, reference)
        ),
        "orders_enabled": False,
    }
    save(HERE / f"comparison_{cost}bp.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=int, choices=(8, 15), default=15)
    args = parser.parse_args()
    result = compare(args.cost)
    print({key: result[key] for key in ("advance", "return_lift_over_boost", "drawdown_change_over_boost")})
