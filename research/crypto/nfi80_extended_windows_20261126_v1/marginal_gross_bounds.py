"""Resolve mixed-order candles only when every admissible exposure gives one decision."""

from collections import defaultdict
from math import fsum, isfinite
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .capacity_2024_drawdown_attribution import market_streams
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .marginal_gross_guard import GROSS_LIMIT


def order_flows(archive, checks):
    entries = defaultdict(dict)
    subsequent = defaultdict(list)
    confirmation = defaultdict(list)
    for trade in stats_from(checked(archive))["trades"]:
        pair = trade["pair"]
        first, *later = trade["orders"]
        assert first["ft_is_entry"] and first["order_filled_timestamp"] == trade["open_timestamp"]
        entries[first["order_filled_timestamp"]][pair] = first["amount"] * first["safe_price"]
        for order in later:
            signed = order["amount"] if order["ft_is_entry"] else -order["amount"]
            subsequent[order["order_filled_timestamp"]].append((pair, signed, trade["open_timestamp"]))
    for row in read(checked(checks)):
        if row["accepted"]:
            confirmation[pd.Timestamp(row["time"]).value // 1_000_000].append(row["pair"])
    return entries, subsequent, confirmation


def bounds(rows, archive, checks, mark):
    entries, subsequent, confirmations = order_flows(archive, checks)
    output = []
    for row in rows:
        if row["decision"] != "unresolved":
            output.append(row)
            continue
        clock = pd.Timestamp(row["first_fill"])
        tick = clock.value // 1_000_000
        order = confirmations[tick]
        assert len(order) == len(entries[tick]) and set(order) == set(entries[tick])
        prior = order[:order.index(row["pair"])]
        nav = row["pre_candle_marked_nav_usdt"]
        base = row["pre_candle_gross_mark_to_nav"] * nav
        projected = base + fsum(entries[tick][p] for p in [*prior, row["pair"]])
        additions, reductions = [], []
        for pair, quantity, opened in subsequent[tick]:
            if opened == tick and pair not in prior:
                continue
            quote = mark(pair, clock)
            assert isfinite(quote) and quote > 0
            flows = additions if quantity > 0 else reductions
            flows.append(quantity * quote)
        low = max(0, projected + fsum(reductions)) / nav
        high = (projected + fsum(additions)) / nav
        decision = "unresolved"
        if high <= GROSS_LIMIT:
            decision = "would_allow"
        elif low > GROSS_LIMIT and row["entry_slot_bounds"][0] > 12:
            decision = "would_block"
        output.append({**row, "original_decision": row["decision"], "decision": decision,
                       "projected_gross_bounds_to_nav": [low, high],
                       "included_additions_usdt": fsum(additions),
                       "included_reductions_usdt": fsum(reductions)})
    return output


def refine_2024(rows, stress):
    refs = stress["account_sources"]["14"]
    checks = stress["entry_occupancy"]["sources"]["stress_initial_entry_checks"]
    plan = read(checked(refs["native_plan"]))
    fingerprints = read(checked(plan["data_receipts"]))

    def mark(pair, clock):
        marks, _, _ = market_streams(plan, fingerprints, pair)
        return float(marks.at[clock])

    return bounds(rows, refs["native_trades"], checks, mark)


def refine_recent(rows, comparison):
    account = comparison["accounts"]["14"]
    proof = read(checked(account["proof"]))
    trades, = (item for item in proof["sources"] if Path(item["path"]).suffix == ".zip")
    checks = receipt(checked(account["execution"]).parent / "account/initial_entry_checks.json")
    plan = read(checked(account["registered_plan"]))
    reader = IndependentShardRead(plan["data_receipts"])

    def mark(pair, clock):
        return float(reader(pair, "5m-mark").set_index("date").at[clock, "open"])

    return bounds(rows, trades, checks, mark)


def main():
    original = receipt(HERE / "marginal_gross_guard_result.json")
    first = read(checked(original))
    freeze = read(checked(first["freeze"]))
    stress = refine_2024(first["stress_2024"], read(checked(freeze["stress"])))
    recent = {fee: refine_recent(rows, read(checked(freeze["recent"][fee])))
              for fee, rows in first["near_two_years"].items()}
    covered = any(row["pair"] in ("ETH/USDT:USDT", "ONG/USDT:USDT")
                  and row["decision"] == "would_block" for row in stress)
    winners = {fee: all(row["decision"] in ("outside_rule", "would_allow") for row in rows
                        if row["over_1pct_of_prior_equity"]) for fee, rows in recent.items()}
    write_once(HERE / "marginal_gross_bounds_result.json", {
        "source": receipt(Path(__file__)), "original": original, "stress_2024": stress, "recent": recent,
        "stress_hit": covered, "large_winners_retained": winners, "advance": covered and all(winners.values()),
        "scope": "Same original cap and evidence. Bound the unresolved projected gross by putting all negative quantity "
                 "changes before the order for the low endpoint, and all positive changes first for the high endpoint. "
                 "Keep the original pre-candle denominator and accepted earlier entry order. No account reruns.",
        "orders_enabled": False,
    })
    print(covered, winners, flush=True)


if __name__ == "__main__":
    main()
