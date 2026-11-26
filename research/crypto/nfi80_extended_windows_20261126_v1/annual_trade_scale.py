"""Measure tail outcomes on the completed native annual wallets."""

import argparse
from math import fsum
from pathlib import Path
from statistics import median

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .inputs import HERE


ORIGINAL = "original"
FIVE_MINUTES = pd.Timedelta(minutes=5)


def load_account(metrics):
    sources = {checked(row).suffix: row for row in metrics["receipts"]
               if Path(row["path"]).suffix in (".zip", ".parquet")}
    assert set(sources) == {".zip", ".parquet"}
    curve = pd.read_parquet(checked(sources[".parquet"])).set_index("date").marked_equity
    assert len(curve) == metrics["mark_points"] and curve.index.is_unique
    source = stats_from(checked(sources[".zip"]))
    rows = {}
    for trade in source["trades"]:
        if trade["exit_reason"] == "force_exit":
            continue
        fills = trade["orders"]
        entries = [order for order in fills if order["ft_is_entry"]]
        first = entries[0]
        clock = pd.Timestamp(first["order_filled_timestamp"], unit="ms", tz="UTC")
        assert clock in curve.index
        reference = curve.get(clock - FIVE_MINUTES)
        entry_margin = first["amount"] * first["safe_price"] / trade["leverage"]
        assert entry_margin > 0
        net = trade["profit_abs"]
        key = (trade["pair"], trade["is_short"], clock)
        assert key not in rows
        rows[key] = {
            "profit": net, "equity_before_entry": float(reference) if reference is not None else None,
            "opening_margin": entry_margin, "entry_fills": len(entries),
            "fill_signature": tuple((order["ft_is_entry"], order["order_filled_timestamp"],
                                     order["safe_price"]) for order in fills),
        }
    assert len(rows) == metrics["natural_closed_trades"]
    return rows


def describe(rows):
    trades = list(rows)
    winners = [row["profit"] for row in trades if row["profit"] > 0]
    fractions = [row["profit"] / row["equity_before_entry"] for row in trades
                 if row["equity_before_entry"] is not None and row["equity_before_entry"] > 0]
    single = [row["profit"] / row["opening_margin"] for row in trades if row["entry_fills"] == 1]
    return {
        "natural_closes": len(trades), "wins_at_least_25_usdt": sum(value >= 25 for value in winners),
        "wins_at_least_50_usdt": sum(value >= 50 for value in winners),
        "trades_with_prior_equity": len(fractions), "wins_at_least_1pct_of_equity": sum(x >= .01 for x in fractions),
        "losses_at_least_1pct_of_equity": sum(x <= -.01 for x in fractions),
        "best_profit_fraction_of_prior_equity": max((x for x in fractions if x > 0), default=None),
        "worst_profit_fraction_of_prior_equity": min((x for x in fractions if x < 0), default=None),
        "single_entry_count": len(single), "single_entry_wins_at_least_10pct_of_margin": sum(x >= .10 for x in single),
        "median_opening_margin_usdt": median(row["opening_margin"] for row in trades) if trades else None,
    }


def compare(control, candidate):
    shared = control.keys() & candidate.keys()
    matches = [(control[key], candidate[key]) for key in sorted(shared)]
    same_fills = [(a, b) for a, b in matches if a["entry_fills"] == b["entry_fills"] == 1
                  and a["fill_signature"] == b["fill_signature"]]
    margin_component = fsum((b["opening_margin"] - a["opening_margin"]) * a["profit"] / a["opening_margin"]
                            for a, b in same_fills)
    pnl_change = fsum(b["profit"] - a["profit"] for a, b in same_fills)
    return {
        "matched_first_entries": len(shared), "original_only_entries": len(control) - len(shared),
        "candidate_only_entries": len(candidate) - len(shared),
        "control_only_net_realized_usdt": fsum(control[key]["profit"] for key in control.keys() - shared),
        "candidate_only_net_realized_usdt": fsum(candidate[key]["profit"] for key in candidate.keys() - shared),
        "single_entries_with_same_fill_prices_and_clocks": len(same_fills),
        "single_entry_matched_net_pnl_change_usdt": pnl_change,
        "same_price_and_clock_opening_margin_component_usdt": margin_component,
        "remaining_net_pnl_change_usdt": pnl_change - margin_component,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    full = HERE / "annual_matched_comparison.json"
    current = full if full.exists() else HERE / "annual_verified_progress.json"
    verified = receipt(args.comparison or current)
    report = read(checked(verified))
    summary = {}
    for cost, matrix in report["accounts_by_cost_bp"].items():
        accounts = {name: load_account(row) for name, row in matrix.items()}
        cases = {}
        for name, trades in accounts.items():
            quarters = {str(q): describe(row for (_, _, clock), row in trades.items() if clock.quarter == q)
                        for q in range(1, 5)}
            cases[name] = {"all": describe(trades.values()), "quarters_by_first_entry": quarters}
            assert cases[name]["all"]["wins_at_least_25_usdt"] == matrix[name]["natural_profit_at_least_25_usdt"]
            assert cases[name]["all"]["wins_at_least_50_usdt"] == matrix[name]["natural_profit_at_least_50_usdt"]
        paired = {name: compare(accounts[ORIGINAL], rows) for name, rows in accounts.items()
                  if name != ORIGINAL and ORIGINAL in accounts}
        summary[cost] = {"accounts": cases, "pairs_compared_to_original": paired}
        if {"fixed_boost_12", "boost_14"} <= accounts.keys():
            summary[cost]["boost_14_compared_to_fixed_boost_12"] = compare(
                accounts["fixed_boost_12"], accounts["boost_14"],
            )
    save(HERE / "annual_trade_scale.json", {
        "comparison_receipt": verified, "verified_native_accounts": report["completed_native_accounts"],
        "scope": "Native realized natural closures. Prior equity is the mark five minutes before the first fill.",
        "decomposition_scope": "The paired margin component is a fixed-fill arithmetic attribution.",
        "costs": summary, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
