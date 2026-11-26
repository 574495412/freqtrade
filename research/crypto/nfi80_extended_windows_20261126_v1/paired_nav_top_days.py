"""Reconcile the already identified concentration dates to filled native campaigns."""

from collections import defaultdict
from math import fsum, isclose
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .recent_two_year_drawdown_audit import attribution


def campaigns(interval):
    return {
        (row["pair"], row["side"], row["opening_fill"]): row["marked_pnl_change_usdt"] / interval["marked_nav_usdt"][0]
        for row in interval["campaigns"]
    }


def decompose(left, right, expected):
    old = campaigns(left)
    new = campaigns(right)
    reference_return = left["marked_nav_change_usdt"] / left["marked_nav_usdt"][0]
    candidate_return = right["marked_nav_change_usdt"] / right["marked_nav_usdt"][0]
    growth_advantage = (1 + candidate_return) / (1 + reference_return) - 1
    assert isclose(growth_advantage, expected, abs_tol=1e-10, rel_tol=0)
    groups = defaultdict(list)
    pairs = defaultdict(list)
    for key in old.keys() | new.keys():
        amount = (new.get(key, 0) - old.get(key, 0)) / (1 + reference_return)
        groups[(key in old, key in new)].append(amount)
        pairs[key[0]].append(amount)
    contributions = {
        "common_campaigns_on_day": fsum(groups[True, True]),
        "campaigns_only_12_on_day": fsum(groups[True, False]),
        "campaigns_only_14_on_day": fsum(groups[False, True]),
    }
    assert isclose(fsum(contributions.values()), expected, abs_tol=1e-9, rel_tol=0)
    return {
        "12_period_return": reference_return, "14_period_return": candidate_return,
        "relative_growth_advantage": growth_advantage, "parts": contributions,
        "by_pair": dict(sorted(((pair, fsum(values)) for pair, values in pairs.items()),
                               key=lambda x: (-abs(x[1]), x[0]))),
    }


def main():
    base = receipt(HERE / "paired_nav_robustness_result.json")
    original = read(checked(base))
    frozen = read(checked(original["freeze"]))
    proof = {}
    for cost, data in original["continuous"].items():
        accounts = read(checked(frozen["primary"][cost]))["accounts"]
        bank = {name: ledger(read(checked(row["proof"]))) for name, row in accounts.items()}
        manifests = [read(checked(row["registered_plan"]))["data_receipts"] for row in accounts.values()]
        assert all(m == manifests[0] for m in manifests)
        details = {}
        for date, expected in data["best_five_days"].items():
            start = pd.Timestamp(date, tz="UTC")
            end = start + pd.Timedelta(days=1)
            snapshots = {name: attribution(wallet, manifests[0], start, end) for name, wallet in bank.items()}
            details[date] = decompose(snapshots["12"], snapshots["14"], expected)
            print(cost, date, details[date]["parts"], flush=True)
        proof[cost] = details
    write_once(HERE / "paired_nav_top_days_result.json", {
        "source": receipt(Path(__file__)), "based_on": base, "by_cost": proof,
        "scope": "Match native campaigns by pair, side and original first fill within each interval. "
                 "Divide their normalized return differences by the gross daily growth of the 12-slot wallet.",
        "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
