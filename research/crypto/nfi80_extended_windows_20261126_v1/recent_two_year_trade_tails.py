"""Account for large wins and losses on the completed native NFI wallets."""

from math import fsum, isclose
from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import COSTS, write_once
from .annual_trade_scale import describe
from .inputs import HERE
from .recent_two_year_attribution import MARK_TOLERANCE, ledger, reports


THRESHOLD = 0.01
BUCKETS = ("large_win", "large_loss", "other", "missing_prior_mark")


def classify(trade):
    previous_mark = trade["equity_before_entry"]
    if previous_mark is None or previous_mark <= 0:
        return "missing_prior_mark"
    ratio = trade["profit"] / previous_mark
    if ratio >= THRESHOLD:
        return "large_win"
    if ratio <= -THRESHOLD:
        return "large_loss"
    return "other"


def sum_buckets(trades):
    profits = {name: [] for name in BUCKETS}
    for trade in trades:
        profits[classify(trade)].append(trade["profit"])
    return {name: {"natural_closes": len(values), "pnl_usdt": fsum(values)}
            for name, values in profits.items()}


def summarize(left, right, previous):
    left_trades = left["trades"]
    right_trades = right["trades"]
    shared = left_trades.keys() & right_trades.keys()
    differences = {}
    accounts = {}
    for slots, wallet in (("12", left), ("14", right)):
        trades = wallet["trades"]
        all_buckets = sum_buckets(trades.values())
        only = sum_buckets(trades[key] for key in sorted(trades.keys() - shared))
        matched = sum_buckets(trades[key] for key in sorted(shared))
        expected = describe(trades.values())
        for name in BUCKETS:
            assert all_buckets[name]["natural_closes"] == only[name]["natural_closes"] + matched[name]["natural_closes"]
            assert isclose(all_buckets[name]["pnl_usdt"], fsum((only[name]["pnl_usdt"], matched[name]["pnl_usdt"])),
                           abs_tol=MARK_TOLERANCE, rel_tol=0)
        assert all_buckets["large_win"]["natural_closes"] == expected["wins_at_least_1pct_of_equity"]
        assert all_buckets["large_loss"]["natural_closes"] == expected["losses_at_least_1pct_of_equity"]
        assert (
            len(trades) - all_buckets["missing_prior_mark"]["natural_closes"]
            == expected["trades_with_prior_equity"]
        )
        accounts[slots] = {"all_natural_closes": all_buckets, "matched_first_fills": matched,
                           "unmatched_first_fills": only, "native_sources": wallet["refs"]}

    for group in ("all_natural_closes", "matched_first_fills", "unmatched_first_fills"):
        a, b = accounts["12"][group], accounts["14"][group]
        pnl = {name: b[name]["pnl_usdt"] - a[name]["pnl_usdt"] for name in BUCKETS}
        differences[group] = {
            "natural_close_count_change": {name: b[name]["natural_closes"] - a[name]["natural_closes"] for name in BUCKETS},
            "pnl_change_usdt": pnl,
            "combined_large_trade_pnl_change_usdt": fsum((pnl["large_win"], pnl["large_loss"])),
            "total_natural_pnl_change_usdt": fsum(pnl.values()),
        }

    assert isclose(differences["unmatched_first_fills"]["total_natural_pnl_change_usdt"],
                   previous["pnl_components_usdt"]["unmatched_natural_trades_net"], abs_tol=MARK_TOLERANCE, rel_tol=0)
    end_adjustments = {name: previous["pnl_components_usdt"][name] for name in ("terminal_positions", "execution_to_mark")}
    observed = float(right["curve"].marked_equity.iloc[-1] - left["curve"].marked_equity.iloc[-1])
    assert isclose(observed, fsum((differences["all_natural_closes"]["total_natural_pnl_change_usdt"],
                                   *end_adjustments.values())), abs_tol=MARK_TOLERANCE, rel_tol=0)
    return {"accounts": accounts, "difference_14_minus_12": differences, "end_adjustments_usdt": end_adjustments,
            "ending_marked_nav_difference_usdt": observed}


def main():
    previous_source = receipt(HERE / "recent_two_year_trade_attribution.json")
    previous = read(checked(previous_source))["comparisons"]
    audit = {}
    for cost in COSTS:
        cost_results = {}
        for period, source, control, candidate in reports(cost):
            comparison = summarize(ledger(control), ledger(candidate), previous[str(cost)][period])
            cost_results[period] = {"account_reconciliation": source, **comparison}
            tail = comparison["difference_14_minus_12"]["all_natural_closes"]
            print(cost, period, tail["natural_close_count_change"],
                  round(tail["combined_large_trade_pnl_change_usdt"], 4), flush=True)
        audit[str(cost)] = cost_results
    write_once(HERE / "recent_two_year_trade_tails.json", {
        "source": receipt(Path(__file__)), "previous_attribution": previous_source,
        "absolute_profit_fraction_threshold": THRESHOLD,
        "scope": "Natural campaign net PnL, relative to marked account equity five minutes before each first fill. "
                 "The accounts start independently in each period. Terminal open positions and marking changes "
                 "are included in the terminal reconciliation only.",
        "comparisons": audit, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
