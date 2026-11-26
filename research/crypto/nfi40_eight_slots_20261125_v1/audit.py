"""Audit native eight-slot wallet and compare it with the sealed six-slot control."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import (
    cash_from_orders, missing_exposure, periods_for, trade_path,
)
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import read, stats_from
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def summarize_campaigns(stats, cutoff):
    opened, completed = {}, {}
    additions = 0
    for trade in stats["trades"]:
        fills = [order["order_filled_timestamp"] for order in trade["orders"] if order["ft_is_entry"]]
        if not fills or fills[0] >= cutoff:
            continue
        key = (trade["pair"], trade["is_short"], fills[0])
        assert key not in opened
        opened[key] = trade
        additions += sum(timestamp < cutoff for timestamp in fills[1:])
        if trade["close_timestamp"] < cutoff and trade["exit_reason"] != "force_exit":
            completed[key] = trade
    return opened, completed, additions


def compare_trade_counts(control, candidate, cutoff):
    old, old_closed, old_additions = summarize_campaigns(control, cutoff)
    new, new_closed, new_additions = summarize_campaigns(candidate, cutoff)
    overlap = old.keys() & new.keys()
    new_only = new.keys() - old.keys()
    return {
        "control_independent_campaigns": len(old),
        "candidate_independent_campaigns": len(new),
        "control_natural_closes": len(old_closed),
        "candidate_natural_closes": len(new_closed),
        "control_additional_entry_fills": old_additions,
        "candidate_additional_entry_fills": new_additions,
        "control_native_rejected_signal_counter": control["rejected_signals"],
        "candidate_native_rejected_signal_counter": candidate["rejected_signals"],
        "matched_by_pair_direction_first_fill": len(overlap),
        "only_control_at_exact_first_fill_key": len(old.keys() - new.keys()),
        "only_candidate_at_exact_first_fill_key": len(new_only),
        "candidate_only_tags": dict(Counter((new[key]["enter_tag"] or "").strip() for key in new_only)),
    }


def independent_nav(plan, trades, grid, native):
    marks_root = Path(plan["datadir"]) / "futures"
    clocks = grid.as_unit("ms").asi8
    observed = native.hourly_mark_equity.to_numpy()
    reconstructed = np.full(len(grid), float(plan["initial_equity_usdt"]))
    counts = np.zeros(len(grid), dtype=int)
    gross = np.zeros(len(grid), dtype=float)
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade["pair"]].append(trade)
    max_cash_error = max_funding_error = 0.0
    for pair, campaigns in grouped.items():
        stem = pair.replace("/", "_").replace(":", "_")
        marks = pd.read_feather(marks_root / f"{stem}-1h-mark.feather").set_index("date").open
        rates = pd.read_feather(marks_root / f"{stem}-1h-funding_rate.feather")
        prices = marks.reindex(grid).to_numpy()
        events = pd.DatetimeIndex(rates.date).as_unit("ms").asi8
        units = rates.funding_rate.to_numpy() * marks.reindex(rates.date).to_numpy()
        assert np.isfinite(prices).all() and np.isfinite(units).all() and (np.diff(events) > 0).all()
        for trade in campaigns:
            cash = cash_from_orders(trade)
            max_cash_error = max(max_cash_error, float(cash["error"]))
            contribution, exposure, _, funding, _ = trade_path(trade, clocks, prices, events, units)
            max_funding_error = max(max_funding_error, abs(funding - trade["funding_fees"]))
            reconstructed += contribution
            gross += exposure
            counts += (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
    nav_error = float(np.max(np.abs(reconstructed - observed)))
    count_error = int(np.max(np.abs(counts - native.open_trades.to_numpy())))
    missing = [missing_exposure(trades, pair) for pair in ("AXS/USDT:USDT", "TRB/USDT:USDT")]
    passed = (nav_error < 3e-5 and max_cash_error < 1e-5 and max_funding_error < 1e-7
              and count_error == 0 and all(row["status"] != "UNKNOWN" for row in missing))
    return {
        "passed": bool(passed), "maximum_nav_error_usdt": nav_error,
        "maximum_campaign_cash_error_usdt": max_cash_error,
        "maximum_native_funding_error_usdt": max_funding_error,
        "maximum_hourly_count_error": count_error,
        "maximum_hourly_gross_to_nav": float(np.max(gross / observed)),
        "missing_rate_exposure": missing,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", required=True, type=int, choices=(8, 15))
    cost = parser.parse_args().cost
    plan = read(HERE / f"plan{cost}.json")
    for record in read(HERE / "freeze.json")["files"]:
        checked(record)
    reference = read(HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json")
    baseline = next(case for case in reference["baselines"] if case["cost_bps_per_side"] == cost)
    baseline_result, baseline_archive, baseline_nav = map(checked, baseline["old_native_outputs_rechecked"])
    directory = ROOT / "user_data/crypto_quant" / HERE.name / f"eight_slots_{cost}bp"
    outcome = checked({"path": directory / "result.json", "sha256": read(HERE / f"process_{cost}bp.json")["result_sha256"]})
    assert read(HERE / f"process_{cost}bp.json")["returncode"] == 0
    config = read(directory / "effective_config.json")
    baseline_config = read(baseline_result.parent / "effective_config.json")
    for key in ("timeframe", "dry_run_wallet", "stake_amount", "tradable_balance_ratio",
                "trading_mode", "margin_mode", "order_types", "fee", "timerange"):
        assert config[key] == baseline_config[key], key
    assert config["max_open_trades"] == 8 and config["dry_run"]
    assert config["exchange"]["pair_whitelist"] == baseline_config["exchange"]["pair_whitelist"] == plan["pairs"]
    for filename, record in plan["candidates"][0]["source_files"].items():
        assert sha(directory / "strategies" / filename) == record["sha256"]
    archive, = (directory / "native_results").glob("*.zip")
    new, original = stats_from(archive), stats_from(baseline_archive)
    assert all(t["leverage"] == 1 and t["fee_open"] == t["fee_close"] == cost / 10000 for t in new["trades"])
    nav_path = directory / "hourly_equity.parquet"
    native, control = (pd.read_parquet(path).set_index("date") for path in (nav_path, baseline_nav))
    grid = pd.date_range(plan["periods"][0]["start"], plan["periods"][0]["end"], freq="h").as_unit("ns")
    assert len(grid) == 14592 and all(frame.index.as_unit("ns").equals(grid) for frame in (native, control))
    native.index = control.index = grid
    new_periods, old_periods = (periods_for(frame.hourly_mark_equity, plan["periods"]) for frame in (native, control))
    assert abs(new_periods[0]["net_return"] - read(outcome)["periods"][0]["net_return"]) < 1e-12
    assert abs(old_periods[0]["net_return"] - baseline["net_return"]) < 1e-12
    cash = independent_nav(plan, new["trades"], grid, native)
    counts = compare_trade_counts(original, new, int(grid[-1].timestamp() * 1000))
    first = new_periods[0]
    checks = {
        "higher_full_net_return": first["net_return"] > old_periods[0]["net_return"],
        "more_independent_campaigns": counts["candidate_independent_campaigns"] > counts["control_independent_campaigns"],
        "no_higher_marked_drawdown": first["hourly_mark_drawdown"] <= old_periods[0]["hourly_mark_drawdown"],
        "modeled_cash_and_funding_reconciled": cash["passed"],
    }
    output = {
        "cost_bps_per_side": cost, "control_periods": old_periods, "candidate_periods": new_periods,
        "counts": counts, "modeled_economics": cash, "checks": checks,
        "passes_prefrozen_screen": all(checks.values()), "candidate_archive_sha256": sha(archive),
        "candidate_curve_sha256": sha(nav_path), "orders_enabled": False,
    }
    save(HERE / f"comparison_{cost}bp.json", output)
    print(checks, counts, cash, first)


if __name__ == "__main__":
    main()
