"""Reconcile the production-style candidate against the archived native account."""

import argparse
from collections import defaultdict
import json
from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import (
    trade_path,
)
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.audit import profit_profile
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import ROOT, checked, read, receipt, save
from research.crypto.nfi40_eight_slots_20261125_v1.audit import compare_trade_counts
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, size_audit

from .register import HERE


def check_pool(plan, folder, trades):
    schedule = read(checked(plan["historical_schedule"]))["days"]
    observed = read(folder / "actual_historical_pairlists.json")
    assert observed.keys() == schedule.keys()
    assert all(row["pairs"] == schedule[day]["pairs"] and len(row["pairs"]) == 80
               for day, row in observed.items())
    inspections = read(folder / "initial_entry_checks.json")
    accepted = [row for row in inspections if row["accepted"]]
    for row in accepted:
        day = pd.Timestamp(row["time"]).date().isoformat()
        assert row["inside_current_pool"] and row["pair"] in schedule[day]["pairs"]
    keys = {(row["pair"], int(pd.Timestamp(row["time"]).timestamp() * 1000)) for row in accepted}
    assert all((trade["pair"], trade["open_timestamp"]) in keys for trade in trades)
    return {"decision_days_checked": len(observed), "accepted_entry_requests": len(accepted)}


def reconstruct_marks(plan, grid, trades, frame, *, candle_reader=None):
    """Rebuild native economic observations from exported fills and frozen marks."""
    path = Path(plan["datadir"]) / "futures"
    clocks = grid.as_unit("ms").asi8
    reconstructed = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
    counts = np.zeros(len(grid), dtype=int)
    gross = np.zeros(len(grid), dtype=float)
    cash_error = 0.0
    groups = defaultdict(list)
    for trade in trades:
        groups[trade["pair"]].append(trade)
    if candle_reader is None:
        def candle_reader(pair, kind):
            key = pair.replace("/", "_").replace(":", "_")
            return pd.read_feather(path / f"{key}-{kind}.feather")
    for pair, campaigns in groups.items():
        marks = candle_reader(pair, "5m-mark").set_index("date").open
        prices = marks.reindex(grid).to_numpy(dtype=float)
        missing = ~np.isfinite(prices) | (prices <= 0)
        rates = candle_reader(pair, "1h-funding_rate")
        hourly_marks = candle_reader(pair, "1h-mark").set_index("date").open
        events = pd.DatetimeIndex(rates.date).as_unit("ms").asi8
        units = rates.funding_rate.to_numpy() * hourly_marks.reindex(rates.date).to_numpy()
        assert (np.diff(events) > 0).all()
        for trade in campaigns:
            opened = trade["open_timestamp"]
            closed = trade["close_timestamp"]
            held = (clocks > opened) & (clocks <= closed)
            funding_events = (events >= opened) & (events <= closed)
            assert not (held & missing).any(), f"Missing held mark for {pair}"
            assert np.isfinite(units[funding_events]).all(), f"Missing funding mark for {pair}"
            contribution, exposure, _, funding, _ = trade_path(
                trade, clocks, np.where(missing, 0.0, prices), events[funding_events], units[funding_events],
            )
            reconstructed += contribution
            counts += held
            gross += exposure
            cash_error = max(cash_error, abs(funding - trade["funding_fees"]))
    equity = frame.marked_equity.to_numpy()
    nav_error = float(np.max(np.abs(reconstructed - equity)))
    open_count_error = int(np.max(np.abs(counts - frame.open_trades.to_numpy())))
    return {
        "passed": nav_error < 3e-5 and cash_error < 1e-7 and open_count_error == 0,
        "maximum_nav_error_usdt": nav_error,
        "maximum_funding_error_usdt": cash_error,
        "maximum_position_count_error": open_count_error,
        "peak_gross_notional_to_equity": float(np.max(gross / equity)),
    }


def audit_short_rebuy_cap(folder, trades):
    limits = []
    for trade in trades:
        tags = set((trade["enter_tag"] or "").split())
        if not trade["is_short"] or not tags or not tags.issubset({"561", "562", "563"}):
            continue
        stakes = [float(order["amount"] * order["safe_price"] / trade["leverage"])
                  for order in trade["orders"] if order["ft_is_entry"]]
        first = stakes[0]
        assert first > 0
        limits.append((fsum(stakes), 2 * first))
    trace = folder / "blocked-short-rebuys.jsonl"
    blocked = 0
    if trace.exists():
        for line in trace.read_text().splitlines():
            row = json.loads(line)
            assert row["filled_entry_stake"] + row["rejected_stake"] > 2 * row["first_stake"] + 1e-9
            blocked += 1
    assert all(actual <= budget or isclose(actual, budget, abs_tol=1e-6)
               for actual, budget in limits)
    return {
        "scoped_trades": len(limits), "blocked_adjustment_requests": blocked,
        "maximum_filled_entry_to_first_stake": max(
            (2 * actual / budget for actual, budget in limits), default=None,
        ),
        "trace": receipt(trace) if trace.exists() else None,
    }


def account(plan, folder, cost, *, check_sizing=True, candle_reader=None):
    case, = plan["candidates"]
    result_path, nav_path = folder / "result.json", folder / "five_minute_equity.parquet"
    result = read(result_path)
    config_path = folder / "effective_config.json"
    config = read(config_path)
    assert read(folder / "registered_plan.json") == plan
    assert read(folder / "fill_cash_audit.json")["passed"]
    assert result["strategy"] == config["strategy"] == case["strategy"]
    assert result["cost_bps_per_side"] == cost and config["fee"] == cost / 10000
    assert config["max_open_trades"] == case["max_open_trades"] and config["dry_run"]
    assert result["orders_enabled"] is False and result["native_terminal_not_used_for_ranking"]
    assert result["source_strategy_files"] == case["source_files"]
    for filename, source in case["source_files"].items():
        checked(source)
        checked({"path": folder / "strategies" / filename, "sha256": source["sha256"]})
    archive, = (folder / "native_results").glob("*.zip")
    stats = stats_from(archive)
    trades, cash = native_trades(plan, folder, case["strategy"], cost)
    curve = pd.read_parquet(nav_path).set_index("date")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="5min").as_unit("ns")
    assert curve.index.as_unit("ns").equals(grid)
    assert curve.marked_equity.iloc[0] == plan["initial_equity_usdt"]
    assert np.isfinite(curve.marked_equity).all() and (curve.marked_equity > 0).all()
    periods = {}
    for window, native in zip(plan["periods"], result["periods"], strict=True):
        values = curve.loc[window["start"]:window["end"], "marked_equity"]
        gain = float(values.iloc[-1] / values.iloc[0] - 1)
        dd = float((1 - values / values.cummax()).max())
        assert window["name"] == native["name"]
        assert isclose(gain, native["net_return"], abs_tol=1e-10)
        assert isclose(dd, native["five_minute_mark_drawdown"], abs_tol=1e-10)
        periods[window["name"]] = {
            "net_return": gain, "drawdown": dd, "ending_equity": float(values.iloc[-1]),
            "marks": len(values),
        }
    cutoff = int(grid[-1].timestamp() * 1000)
    profile = profit_profile(stats, cutoff)
    net_pnl = periods["continuous"]["ending_equity"] - plan["initial_equity_usdt"]
    profile["top_five_profit_as_fraction_of_full_net_pnl"] = (
        profile["top_five_profit_usdt"] / net_pnl if net_pnl else None
    )
    summary = {
        "periods": periods, "profits": profile, "cash": cash,
        "five_minute_nav_reconciliation": reconstruct_marks(
            plan, grid, stats["trades"], curve, candle_reader=candle_reader
        ),
        "pool": check_pool(plan, folder, trades),
        "sizing": size_audit(plan, folder, "FlatTopBoost", trades) if check_sizing else None,
        "max_observed_positions": int(curve.open_trades.max()),
        "marks_at_active_ceiling": int(curve.open_trades.eq(case["max_open_trades"]).sum()),
        "liquidations": sum(trade["exit_reason"] == "liquidation" for trade in trades),
        "native_model_funding_usdt": fsum(trade["funding_fees"] for trade in stats["trades"]),
        "sources": [receipt(path) for path in (result_path, archive, nav_path, config_path)],
    }
    if case["strategy"] == "FlatBoostShortRebuyCap":
        summary["short_rebuy_cap"] = audit_short_rebuy_cap(folder, trades)
    return summary, stats, invariant_config(config)


def main(cost, baseline_only, study, *, plan_path=None, candidate_folder=None, completion_path=None):
    plan = read(plan_path or study / "plan.json")
    for key in ("predecessor_plan", "protocol_receipt", "online_alignment"):
        checked(plan[key])
    predecessor = read(checked(plan["predecessor_plan"]))
    aligned = read(checked(plan["online_alignment"]))["native_historical_reference"]
    recorded = next(row for row in aligned["accounts"] if row["cost_bps_per_side"] == cost)
    original_result, *_ = map(checked, recorded["native_receipts"])
    fixed, fixed_trades, fixed_config = account(predecessor, original_result.parent, cost)
    if baseline_only:
        assert fixed["five_minute_nav_reconciliation"]["passed"]
        save(study / f"baseline_preflight_{cost}bp.json", fixed)
        print(fixed["five_minute_nav_reconciliation"], fixed["periods"]["continuous"])
        return
    completion = read(completion_path or study / f"execution_{cost}bp.json")
    folder = candidate_folder or (ROOT / "user_data/crypto_quant" / study.name / f"cost_{cost}bp")
    assert completion["returncode"] == 0 and Path(completion["output"]) == folder
    assert all(plan[key] == predecessor[key] for key in (
        "engine_manifest", "engine_commit", "pairs", "historical_schedule", "start", "end",
        "comparison_end", "periods", "data_receipts", "datadir", "market_snapshot",
        "observation_frequency", "allowed_leverage", "initial_equity_usdt",
    ))
    candidate, new_trades, new_config = account(plan, folder, cost)
    assert fixed_config == new_config
    cutoff = int(pd.Timestamp(plan["comparison_end"]).timestamp() * 1000)
    old, new = fixed["periods"]["continuous"], candidate["periods"]["continuous"]
    checks = {
        "higher_marked_return": new["net_return"] > old["net_return"],
        "no_higher_five_minute_drawdown": new["drawdown"] <= old["drawdown"],
        "independent_nav_and_funding_reconciled": all(
            row["five_minute_nav_reconciliation"]["passed"] for row in (fixed, candidate)
        ),
    }
    output = {
        "cost_bps_per_side": cost,
        "control": fixed, "candidate": candidate,
        "return_lift": new["net_return"] - old["net_return"],
        "drawdown_change": new["drawdown"] - old["drawdown"],
        "counts": compare_trade_counts(fixed_trades, new_trades, cutoff),
        "checks": checks, "advance": all(checks.values()),
        "orders_enabled": False, "production_eligible": False,
    }
    save(study / f"comparison_{cost}bp.json", output)
    print(output["checks"], output["return_lift"], output["drawdown_change"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=int, choices=(15, 8), default=15)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--study", type=Path, default=HERE)
    args = parser.parse_args()
    main(args.cost, args.baseline_only, args.study.resolve(strict=True))
