"""Verify unchanged-price execution windows for both sides on the five largest days."""

from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .native_trade_path_review import books, timestamp
from .top_day_execution import capture, prints


def time_windows(rows, rate, qty, short, remaining_seconds):
    at_limit = rows.price.ge(rate) if short else rows.price.le(rate)
    opposing = ~rows.seller_taker if short else rows.seller_taker
    windows = {}
    for delay in (0, 1, 5, 15):
        lo, hi = delay * 1000, (delay + remaining_seconds) * 1000
        assert hi <= 300_000
        live = rows.ms.ge(lo) & rows.ms.lt(hi)
        reached = rows.loc[live & at_limit]
        available = float(rows.loc[live & at_limit & opposing, "amount"].sum())
        market_prints = rows.loc[live, "price"]
        nearest = (market_prints.max() if short else market_prints.min()) if not market_prints.empty else None
        windows[str(delay)] = {
            "touch_count": len(reached), "observed_opposite_taker_qty_over_order": available / qty,
            "enough_opposite_taker_qty": bool(available >= qty),
            "most_favorable_print": float(nearest) if nearest is not None else None,
        }
    return windows


@lru_cache(maxsize=24)
def source_rows(symbol, clock):
    source = capture(symbol, clock)
    return source, prints(source, clock)


def analyze(comparison, top):
    report = comparison["accounts"]["14"]
    cfg = read(checked(report["effective_config"]))
    assert cfg["unfilledtimeout"]["unit"] == "minutes" and cfg["order_types"]["entry"] == "limit"
    wait = cfg["unfilledtimeout"]["entry"] * 60
    _, book = books(read(checked(report["proof"])))
    outcomes = []
    for row in comparison["fill_and_nav_attribution"]["trade_changes"]["native_entries_only_14"]:
        pair, clock = row["pair"], pd.Timestamp(row["first_fill"])
        if clock.date().isoformat() not in top:
            continue
        short = row["side"] == "short"
        order = book[(pair, short, clock)]["orders"][0]
        assert order["ft_is_entry"] and timestamp(order["order_filled_timestamp"]) == clock
        symbol = pair.split("/")[0] + "USDT"
        source, tape = source_rows(symbol, clock)
        amount, limit = order["amount"], order["safe_price"]
        outcomes.append({
            **row, "input_trades": source, "original_limit": limit, "original_quantity": amount,
            "fraction_of_five_minute_prints": float(amount / tape.amount.sum()),
            "by_latency": time_windows(tape, limit, amount, short, wait),
        })
        print(pair, row["side"], clock, outcomes[-1]["by_latency"]["5"]["enough_opposite_taker_qty"], flush=True)
    return outcomes


def summarize(rows):
    result = {}
    for seconds in ("0", "1", "5", "15"):
        subset = [row for row in rows if not row["by_latency"][seconds]["enough_opposite_taker_qty"]]
        result[seconds] = {
            "insufficient_print_qty": len(subset),
            "insufficient_among_large_wins": sum(row["over_1pct_of_prior_equity"] for row in subset),
            "by_side": dict(Counter(row["side"] for row in subset)),
        }
    return result


def main():
    days_source = receipt(HERE / "paired_nav_robustness_result.json")
    dates = read(checked(days_source))["continuous"]
    top = set(dates["15"]["best_five_days"])
    assert top == set(dates["8"]["best_five_days"]) and len(top) == 5
    comparisons = {fee: receipt(HERE / f"recent_two_year_{fee}bp_comparison.json") for fee in ("15", "8")}
    freeze = write_once(HERE / "top_days_all_entries_freeze.json", {
        "source": receipt(Path(__file__)), "protocol": receipt(HERE / "top_days_all_entries_protocol.json"),
        "day_list": days_source, "days": sorted(top), "comparisons": comparisons, "orders_enabled": False,
    })
    cases = {fee: analyze(read(checked(ref)), top) for fee, ref in comparisons.items()}
    result = {fee: {"checked_fills": len(rows), "large_wins": sum(r["over_1pct_of_prior_equity"] for r in rows),
                    "latency_summary": summarize(rows), "fills": rows}
              for fee, rows in cases.items()}
    write_once(HERE / "top_days_all_entries_result.json", {
        "freeze": freeze, "by_fee": result, "orders_enabled": False, "live_execution_proven": False,
    })
    print({k: v["latency_summary"] for k, v in result.items()}, flush=True)


if __name__ == "__main__":
    main()
