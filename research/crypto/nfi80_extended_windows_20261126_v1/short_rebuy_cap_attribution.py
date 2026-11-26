"""Attribute verified cap-vs-original accounts from marked equity and native orders."""

import argparse
from collections import defaultdict
import json
from math import fsum
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .fourteen_cost_review import side_totals
from .inputs import HERE
from .native_trade_path_review import books, timestamp
from .recent_two_year_attribution import attribution
from .recent_two_year_trade_tails import summarize


def order_path(trade):
    ordered = [o for o in trade["orders"] if o["ft_is_entry"]]
    margins = [o["amount"] * o["safe_price"] / trade["leverage"] for o in ordered]
    return {
        "opened": timestamp(trade["open_timestamp"]).isoformat(),
        "closed": timestamp(trade["close_timestamp"]).isoformat(),
        "added_fills": len(ordered) - 1,
        "first_margin_usdt": margins[0], "cumulative_margin_usdt": fsum(margins),
        "net_usdt": trade["profit_abs"], "exit_reason": trade["exit_reason"],
    }


def cap_occurrences(trace, sampled, original):
    if trace is None:
        return []
    related = defaultdict(list)
    for line in checked(trace).read_text().splitlines():
        row = json.loads(line)
        moment = pd.Timestamp(row["time"])
        matches = [key for key, trade in sampled.items()
                   if key[0] == row["pair"] and key[1] and trade["enter_tag"].split() == row["entry_tag"].split()
                   and key[2] <= moment <= timestamp(trade["close_timestamp"])]
        assert len(matches) == 1 and row["filled_entry_stake"] + row["rejected_stake"] > 2 * row["first_stake"] + 1e-9
        related[matches[0]].append(row)
    observations = []
    for key, requests in sorted(related.items()):
        old = original.get(key)
        observations.append({
            "pair": key[0], "first_filled_clock": key[2].isoformat(),
            "capped": order_path(sampled[key]), "uncapped": order_path(old) if old is not None else None,
            "rejected_requests": len(requests), "first_rejection": requests[0]["time"],
            "last_rejection": requests[-1]["time"],
        })
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cost", type=int, choices=(15, 8))
    cost = parser.parse_args().cost
    source = receipt(HERE / f"short_rebuy_14_wallet_{cost}bp_verified.json")
    control = read(checked(source))
    incumbent = read(checked(control["control"]))["account"]
    reference, old_trades = books(read(checked(incumbent["proof"])))
    candidate, new_trades = books(read(checked(control["proof"])))
    result = attribution(reference, candidate)
    paths = cap_occurrences(control["verified_budget"]["trace"], new_trades, old_trades)
    assert sum(x["rejected_requests"] for x in paths) == control["verified_budget"]["blocked_adjustment_requests"]
    write_once(HERE / f"short_rebuy_14_wallet_{cost}bp_attribution.json", {
        "source": receipt(Path(__file__)), "comparison": source,
        "marks_and_fills": result, "large_wins_and_losses": summarize(reference, candidate, result),
        "natural_pnl_by_direction": {"uncapped": side_totals(reference), "capped": side_totals(candidate)},
        "blocked_order_campaigns": paths, "orders_enabled": False,
    })
    print(cost, "terminal NAV change", result["ending_equity_gain_usdt"], "affected campaigns", len(paths), flush=True)


if __name__ == "__main__":
    main()
