"""Compare both completed native accounts on each account's worst drawdown interval."""

import argparse
from math import fsum, isclose
from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import COSTS, write_once
from .capacity_2024_drawdown_attribution import worst_drawdown
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .recent_two_year_drawdown_audit import attribution


def compare_interval(left, right):
    left_nav, right_nav = left["marked_nav_usdt"][0], right["marked_nav_usdt"][0]
    left_return = left["marked_nav_change_usdt"] / left_nav
    right_return = right["marked_nav_change_usdt"] / right_nav
    left_pairs, right_pairs = left["change_by_pair_usdt"], right["change_by_pair_usdt"]
    pairs = [
        {
            "pair": pair,
            "marked_return_contribution_14_minus_12": (
                right_pairs.get(pair, 0) / right_nav - left_pairs.get(pair, 0) / left_nav
            ),
        }
        for pair in sorted(left_pairs.keys() | right_pairs.keys())
    ]
    delta = right_return - left_return
    attributed = fsum(row["marked_return_contribution_14_minus_12"] for row in pairs)
    if not isclose(attributed, delta, abs_tol=1e-9, rel_tol=0):
        raise ValueError("Per-pair contributions do not reconcile to the account return difference")
    return {
        "account_returns": {"12": left_return, "14": right_return},
        "return_difference": delta,
        "pairs": sorted(pairs, key=lambda row: row["marked_return_contribution_14_minus_12"]),
    }


def main():
    argument = argparse.ArgumentParser(description=__doc__)
    argument.add_argument("--cost", type=int, choices=COSTS, default=15)
    cost = argument.parse_args().cost
    source = receipt(HERE / f"recent_two_year_{cost}bp_comparison.json")
    rows = read(checked(source))["accounts"]
    accounts = {name: ledger(read(checked(row["proof"]))) for name, row in rows.items()}
    manifests = {
        name: read(checked(row["registered_plan"]))["data_receipts"] for name, row in rows.items()
    }
    if manifests["12"] != manifests["14"]:
        raise ValueError("Both accounts must use the same market data")
    if not accounts["12"]["curve"].index.equals(accounts["14"]["curve"].index):
        raise ValueError("Both accounts must have the same five-minute marks")
    outcomes = {}
    for owner in ("12", "14"):
        peak, trough, drawdown = worst_drawdown(accounts[owner]["curve"].marked_equity)
        expected_drawdown = rows[owner]["periods"]["continuous"]["drawdown"]
        if not isclose(drawdown, expected_drawdown, abs_tol=1e-12, rel_tol=0):
            raise ValueError(f"The {owner}-slot peak-to-trough drawdown differs from its report")
        observed = {
            name: attribution(wallet, manifests[name], peak, trough)
            for name, wallet in accounts.items()
        }
        comparison = compare_interval(observed["12"], observed["14"])
        if not isclose(-comparison["account_returns"][owner], drawdown, abs_tol=1e-12, rel_tol=0):
            raise ValueError(f"The {owner}-slot attributed drawdown differs from its marks")
        outcomes[owner] = {
            "peak": peak.isoformat(),
            "trough": trough.isoformat(),
            "drawdown": drawdown,
            "native_path_comparison": comparison,
            "accounts": observed,
        }
        print(owner, peak, trough, comparison["account_returns"], flush=True)
    write_once(HERE / f"recent_two_year_{cost}bp_drawdown_comparison.json", {
        "source": receipt(Path(__file__)), "comparison": source,
        "scope": (
            "Reconciled native fills and 5m mark/funding paths. "
            "Each account's return uses its own NAV at the first mark of the interval."
        ),
        "intervals_by_peak_owner": outcomes, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
