"""Reconcile the return advantage of 14 slots from completed native accounts."""

from math import fsum, isclose
from pathlib import Path
from statistics import median

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import COSTS, write_once
from .annual_trade_scale import FIVE_MINUTES, compare, describe, load_account
from .inputs import HERE


MARK_TOLERANCE = 1e-6
ARCHIVE = HERE.parent / "nfi80_online_baseline_20261125_v1"


def reports(cost):
    annual = HERE / ("boost14_2025_15bp_frozen_basis.json" if cost == 15 else "annual_2025_boost14_8bp_verified.json")
    for period, path in (
        ("2025", annual), ("2026_to_nov09", ARCHIVE / f"comparison_{cost}bp.json"),
        ("2026_nov10_to_nov25_restart", HERE / f"recent_window_{cost}bp_controls.json"),
    ):
        source = receipt(path)
        accounts = read(checked(source))
        assert accounts.get("cost_bps_per_side", accounts.get("cost_per_side_bp")) == cost
        if period == "2026_to_nov09":
            yield period, source, accounts["control"], accounts["candidate"]
        else:
            yield period, source, accounts["accounts"]["fixed_boost_12"], accounts["accounts"]["boost_14"]


def ledger(account):
    receipts = account.get("receipts", account.get("sources"))
    refs = {Path(item["path"]).suffix: item for item in receipts
            if Path(item["path"]).suffix in (".zip", ".parquet")}
    trades = stats_from(checked(refs[".zip"]))["trades"]
    curve = pd.read_parquet(checked(refs[".parquet"])).set_index("date")
    if "periods" in account:
        assert account["five_minute_nav_reconciliation"]["passed"]
        summary = account["periods"]["continuous"]
        counts = summary["marks"], account["profits"]["natural_closes"]
        ending = summary["ending_equity"]
    else:
        proof, = [item for item in receipts if Path(item["path"]).name == "account_reconciled.json"]
        assert read(checked(proof))["five_minute_nav_reconciliation"]["passed"]
        counts = account["mark_points"], account["natural_closed_trades"]
        ending = account["final_equity_usdt"]
    natural = load_account({"receipts": list(refs.values()), "mark_points": counts[0], "natural_closed_trades": counts[1]})
    first, last = curve.iloc[0], curve.iloc[-1]
    assert float(first.marked_equity) == 1000 and curve.index.is_unique and curve.index.is_monotonic_increasing
    assert isclose(float(last.marked_equity), ending, abs_tol=MARK_TOLERANCE, rel_tol=0)
    forced = [trade for trade in trades if trade["exit_reason"] == "force_exit"]
    archived_net = fsum(trade["profit_abs"] for trade in trades)
    assert len(natural) + len(forced) == len(trades)
    assert isclose(archived_net + float(first.marked_equity), float(last.equity), abs_tol=MARK_TOLERANCE, rel_tol=0)
    assert isclose(fsum(row["profit"] for row in natural.values()), float(last.closed_realized_pnl),
                   abs_tol=MARK_TOLERANCE, rel_tol=0)
    terminal_position_pnl = fsum(trade["profit_abs"] for trade in forced)
    assert isclose(terminal_position_pnl, float(last.open_pnl), abs_tol=MARK_TOLERANCE, rel_tol=0)
    return {
        "curve": curve, "trades": natural, "refs": refs,
        "forced_position_pnl_usdt": terminal_position_pnl,
        "execution_to_mark_delta_usdt": float(last.marked_equity - last.equity),
    }


def incremental_trades(left, right):
    common = left["trades"].keys() & right["trades"].keys()
    equal, remaining = [], []
    for key in sorted(common):
        a, b = left["trades"][key], right["trades"][key]
        group = equal if a["entry_fills"] == b["entry_fills"] == 1 and a["fill_signature"] == b["fill_signature"] else remaining
        group.append((a, b))

    def passes(trade):
        reference = trade["equity_before_entry"]
        return reference is not None and reference > 0 and trade["profit"] >= .01 * reference

    def appeared(whose, comparison):
        rows = []
        for key in sorted(whose["trades"].keys() - comparison["trades"].keys()):
            pair, short, time = key
            other = comparison["curve"].open_trades.get(time - FIVE_MINUTES)
            trade = whose["trades"][key]
            rows.append({"pair": pair, "side": "short" if short else "long", "first_fill": time.isoformat(),
                         "profit_usdt": trade["profit"], "over_1pct_of_prior_equity": passes(trade),
                         "other_account_positions_on_prior_mark": int(other) if other is not None else None})
        return rows

    return {
        "fixed_fill_size_ratio_median": median(b["opening_margin"] / a["opening_margin"] for a, b in equal),
        "matched_other_natural_pnl_change_usdt": fsum(b["profit"] - a["profit"] for a, b in remaining),
        "cross_above_1pct_matched_same_fills": sum(passes(b) and not passes(a) for a, b in equal),
        "cross_below_1pct_matched_same_fills": sum(passes(a) and not passes(b) for a, b in equal),
        "cross_above_1pct_other_matched": sum(passes(b) and not passes(a) for a, b in remaining),
        "cross_below_1pct_other_matched": sum(passes(a) and not passes(b) for a, b in remaining),
        "native_entries_only_14": appeared(right, left), "native_entries_only_12": appeared(left, right),
    }


def attribution(left, right):
    l, r = (data["curve"] for data in (left, right))
    assert l.index.equals(r.index)
    delta = float(r.marked_equity.iloc[-1] - l.marked_equity.iloc[-1])
    paired = compare(left["trades"], right["trades"])
    extras = incremental_trades(left, right)
    terminal = right["forced_position_pnl_usdt"] - left["forced_position_pnl_usdt"]
    marking = right["execution_to_mark_delta_usdt"] - left["execution_to_mark_delta_usdt"]
    pieces = {
        "entry_size_same_fills": paired["same_price_and_clock_opening_margin_component_usdt"],
        "same_fill_residual": paired["remaining_net_pnl_change_usdt"],
        "other_matched_natural_trades": extras["matched_other_natural_pnl_change_usdt"],
        "unmatched_natural_trades_net": paired["candidate_only_net_realized_usdt"] - paired["control_only_net_realized_usdt"],
        "terminal_positions": terminal, "execution_to_mark": marking,
    }
    assert isclose(fsum(pieces.values()), delta, abs_tol=MARK_TOLERANCE, rel_tol=0)
    tails = {slots: describe(data["trades"].values()) for slots, data in (("12", left), ("14", right))}
    threshold_change = fsum((extras["cross_above_1pct_matched_same_fills"], -extras["cross_below_1pct_matched_same_fills"],
                            extras["cross_above_1pct_other_matched"], -extras["cross_below_1pct_other_matched"],
                            sum(row["over_1pct_of_prior_equity"] for row in extras["native_entries_only_14"]),
                            -sum(row["over_1pct_of_prior_equity"] for row in extras["native_entries_only_12"])))
    assert threshold_change == tails["14"]["wins_at_least_1pct_of_equity"] - tails["12"]["wins_at_least_1pct_of_equity"]
    return {
        "start": l.index[0].isoformat(), "comparison_end": l.index[-1].isoformat(), "marks": len(l),
        "ending_equity_gain_usdt": delta, "pnl_components_usdt": pieces,
        "reconciliation_error_usdt": delta - fsum(pieces.values()),
        "tails": tails, "matched_natural_trades": paired, "trade_changes": extras,
        "native_receipts": {"12": left["refs"], "14": right["refs"]},
    }


def main():
    payload = {"scope": "Arithmetic attribution on matched completed wallets. Periods have separate initial cash; no stitching or unobserved counterfactual fills.",
               "source": receipt(Path(__file__)), "orders_enabled": False, "comparisons": {}}
    for cost in COSTS:
        cases = {}
        for period, summary, a, b in reports(cost):
            result = attribution(ledger(a), ledger(b))
            cases[period] = {"reference_reconciliation": summary, **result}
            print(cost, period, round(result["ending_equity_gain_usdt"], 4),
                  {name: round(x, 4) for name, x in result["pnl_components_usdt"].items()}, flush=True)
        payload["comparisons"][str(cost)] = cases
    write_once(HERE / "recent_two_year_trade_attribution.json", payload)


if __name__ == "__main__":
    main()
