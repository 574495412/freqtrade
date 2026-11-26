"""Trace where matched NFI runs first diverge in native entry processing."""

from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE


STEP = pd.Timedelta(minutes=5)
ENGINE_FILE = "freqtrade/optimize/backtesting.py"
ATTRIBUTION = HERE / "recent_two_year_trade_attribution.json"


def account(sources, slots):
    archive = checked(sources[".zip"])
    observed = checked(sources[".parquet"])
    workspace = archive.parents[1]
    plan = receipt(workspace / "registered_plan.json")
    checks = receipt(workspace / "initial_entry_checks.json")
    setup = read(checked(plan))
    candidate, = setup["candidates"]
    native = stats_from(archive)
    assert native["max_open_trades"] == candidate["max_open_trades"] == slots
    assert not any(native[name] for name in ("locks", "timedout_entry_orders", "canceled_trade_entries", "replaced_entry_orders"))
    engine = read(checked(setup["engine_manifest"]))
    entry_rule = {"path": str(Path(setup["engine_source"]) / ENGINE_FILE), "sha256": engine["files"][ENGINE_FILE]}
    rule = checked(entry_rule).read_text()
    assert "self.trade_slot_available(LocalTrade.bt_open_open_trade_count)" in rule
    assert "max_open_trades <= 0 or open_trade_count < max_open_trades" in rule
    assert setup["allowed_leverage"] == 3 and setup["initial_equity_usdt"] == 1000
    verified = list(candidate["source_files"].values())
    for row in verified:
        checked(row)
    return {
        "nav": pd.read_parquet(observed).set_index("date"), "native": native, "approvals": read(checked(checks)),
        "setup": setup, "sources": candidate["source_files"],
        "receipts": {**sources, "plan": plan, "approvals": checks, "native_entry_rule": entry_rule},
    }


def orders_before(trades, time_ms):
    return sorted((order["order_filled_timestamp"], trade["pair"], trade["is_short"], order["ft_is_entry"],
                   order["ft_order_side"], order["safe_price"], order["amount"], order["ft_order_tag"] or "")
                  for trade in trades for order in trade["orders"] if order["order_filled_timestamp"] < time_ms)


def entries_at(case, t):
    ms = t.value // 1_000_000
    orders = {}
    for trade in case["native"]["trades"]:
        entry = next(row for row in trade["orders"] if row["ft_is_entry"])
        if entry["order_filled_timestamp"] != ms:
            continue
        pair = trade["pair"]
        assert pair not in orders
        orders[pair] = {"side": "short" if trade["is_short"] else "long", "tag": trade["enter_tag"].strip(),
                        "entry_margin_usdt": entry["amount"] * entry["safe_price"] / trade["leverage"],
                        "first_fill": entry, "net_pnl_usdt": trade["profit_abs"],
                        "close": trade["close_date"], "exit_reason": trade["exit_reason"]}
    attempts = [row for row in case["approvals"] if pd.Timestamp(row["time"]) == t]
    sequence = [row["pair"] for row in attempts]
    assert all(row["accepted"] and row["inside_current_pool"] for row in attempts)
    assert len(sequence) == len(set(sequence)) and set(sequence) == set(orders)
    closes = sum(trade["close_timestamp"] == ms for trade in case["native"]["trades"])
    assert closes == 0
    return sequence, orders


def proof(original, expanded, differences):
    a, b = original["setup"], expanded["setup"]
    for field in ("engine_commit", "engine_manifest", "historical_schedule", "data_receipts", "market_snapshot"):
        assert a[field] == b[field], field
    for name, ref in original["sources"].items():
        assert expanded["sources"][name]["sha256"] == ref["sha256"], name
    labs = differences["trade_changes"]["native_entries_only_14"]
    first = pd.Timestamp(min(row["first_fill"] for row in labs))
    next_mark = first + STEP
    first_ms = first.value // 1_000_000
    prefix = original["nav"].loc[:first]
    pd.testing.assert_frame_equal(prefix, expanded["nav"].loc[:first], check_exact=True)
    a_orders, b_orders = (orders_before(data["native"]["trades"], first_ms) for data in (original, expanded))
    assert a_orders == b_orders
    order_a, fills_a = entries_at(original, first)
    order_b, fills_b = entries_at(expanded, first)
    assert order_b[:len(order_a)] == order_a
    assert all(fills_a[pair]["first_fill"] == fills_b[pair]["first_fill"] for pair in order_a)
    added = order_b[len(order_a):]
    assert set(added) == {row["pair"] for row in labs if pd.Timestamp(row["first_fill"]) == first}
    size_before = int(prefix.open_trades.iloc[-1])
    size_after = {slots: int(data["nav"].at[next_mark, "open_trades"])
                  for slots, data in (("12", original), ("14", expanded))}
    assert size_before + len(order_a) == size_after["12"] == 12
    assert size_before + len(order_b) == size_after["14"] and size_after["14"] <= 14
    return {
        "first_divergent_candle": first.isoformat(), "following_observation": next_mark.isoformat(),
        "identical_prior_marks": len(prefix), "identical_prior_filled_orders": len(a_orders),
        "positions_before": size_before, "positions_after": size_after, "same_first_fills_in_order": order_a,
        "added_entries_in_order": [{"pair": pair, **fills_b[pair]} for pair in added],
        "marked_equity_before_usdt": float(prefix.marked_equity.iloc[-1]),
        "marked_equity_on_next_observation_usdt": {"12": float(original["nav"].at[next_mark, "marked_equity"]),
                                                   "14": float(expanded["nav"].at[next_mark, "marked_equity"])},
        "native_rejected_slots_full_window": {slots: int(data["native"]["rejected_signals"])
                                              for slots, data in (("12", original), ("14", expanded))},
        "receipts": {"12": original["receipts"], "14": expanded["receipts"]},
    }


def main():
    source = receipt(ATTRIBUTION)
    available = read(checked(source))["comparisons"]
    events = {}
    for cost, by_period in available.items():
        events[cost] = {}
        for period in ("2025", "2026_to_nov09"):
            case = by_period[period]
            wallets = {slots: account(case["native_receipts"][str(slots)], slots) for slots in (12, 14)}
            event = proof(wallets[12], wallets[14], case)
            events[cost][period] = event
            print(cost, period, event["first_divergent_candle"], event["positions_before"],
                  event["positions_after"], [row["pair"] for row in event["added_entries_in_order"]], flush=True)
    write_once(HERE / "entry_capacity_attribution.json", {
        "scope": "First entry-sequence divergence and native capacity at that candle. Evidence does not classify every later unmatched fill.",
        "source": source, "implementation": receipt(Path(__file__)), "events": events, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
