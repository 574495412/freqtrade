"""Reconcile cap stop sizes and test a fixed next-candle exit-price diagnostic."""

from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .native_trade_path_review import books, price_reader, timestamp


def exit_orders(trade):
    entries = fsum(row["amount"] for row in trade["orders"] if row["ft_is_entry"])
    exits = [row for row in trade["orders"] if not row["ft_is_entry"]]
    assert isclose(fsum(row["amount"] for row in exits), entries, abs_tol=1e-7)
    summary = [
        {
            "time_utc": timestamp(row["order_filled_timestamp"]).isoformat(),
            "amount": row["amount"], "price": row["safe_price"], "tag": row.get("ft_order_tag"),
            "fraction_of_all_entry_quantity": row["amount"] / entries,
        } for row in exits
    ]
    return {"fills": summary, "reason": trade["exit_reason"], "last_close_fraction": summary[-1]["fraction_of_all_entry_quantity"]}


def covered_trade(row, wallet, trades, trade_prices, read_marks):
    opened = pd.Timestamp(row["first_filled_clock"])
    first_reject = pd.Timestamp(row["first_rejection"])
    clock = first_reject + FIVE_MINUTES
    key = row["pair"], True, opened
    trade = trades[key]
    end = timestamp(trade["close_timestamp"])
    initial = wallet["trades"][key]["equity_before_entry"]
    assert initial > 0 and opened < first_reject
    if clock > end:
        return {**row, "status": "already_exited", "exit_orders": exit_orders(trade)}
    prices = trade_prices(key[0], "5m-futures").set_index("date")
    tape = prices.loc[clock]
    assert np.isfinite(tape.open) and tape.open > 0 and tape.volume > 0
    marks, events, funding = read_marks(key[0])
    times = pd.DatetimeIndex([clock, end + FIVE_MINUTES]).as_unit("ms").asi8
    admitted = (events >= trade["open_timestamp"]) & (events <= trade["close_timestamp"])
    pnl, gross, _, total_funding, _ = independent_account_audit_v2.trade_path(
        trade, times, np.asarray([tape.open, 0.0]), events[admitted], funding[admitted],
    )
    assert isclose(trade["funding_fees"], total_funding, abs_tol=1e-7, rel_tol=0)
    assert isclose(trade["profit_abs"], pnl[1], abs_tol=1e-6, rel_tol=0)
    assert gross[0] > 0 and np.isfinite(funding[admitted]).all()
    mark = float(marks.loc[clock])
    assert mark > 0
    return {
        **row, "status": "estimated", "next_bar_utc": clock.isoformat(), "cover_price_proxy": float(tape.open),
        "concurrent_mark_price": mark, "pre_entry_nav_usdt": initial, "next_bar_net_usdt": float(pnl[0]),
        "next_bar_vs_capped_actual_usdt": float(pnl[0] - pnl[1]),
        "next_bar_net_fraction_of_prior_nav": float(pnl[0] / initial),
        "capped_actual_fraction_of_prior_nav": trade["profit_abs"] / initial,
        "open_quantity_before_cover": float(gross[0] / tape.open), "exit_orders": exit_orders(trade),
    }


def main():
    anchor = receipt(HERE / "short_rebuy_14_wallet_15bp_attribution.json")
    protocol = receipt(HERE / "first_cap_exit_protocol.json")
    preflight = receipt(HERE / "short_rebuy_14_wallet_preflight.json")
    freeze = write_once(HERE / "first_cap_exit_freeze.json", {
        "source": receipt(Path(__file__)), "cap_attribution": anchor, "protocol": protocol,
        "preflight": preflight, "observed_proxy_results_at_freeze": False, "orders_enabled": False,
    })
    reference = read(checked(anchor))
    capped = read(checked(reference["comparison"]))
    plan = read(checked(read(checked(preflight))["by_cost"]["15"]["plan"]))
    wallet, book = books(read(checked(capped["proof"])))
    reader = IndependentShardRead(plan["data_receipts"])
    marks = price_reader(plan)
    paths = [covered_trade(row, wallet, book, reader, marks) for row in reference["blocked_order_campaigns"]]
    estimates = [row for row in paths if row["status"] == "estimated"]
    total = fsum(row["next_bar_vs_capped_actual_usdt"] for row in estimates)
    old_wins = sum(row["capped_actual_fraction_of_prior_nav"] >= .01 for row in estimates)
    new_wins = sum(row["next_bar_net_fraction_of_prior_nav"] >= .01 for row in estimates)
    old_losses = sum(row["capped_actual_fraction_of_prior_nav"] <= -.01 for row in estimates)
    new_losses = sum(row["next_bar_net_fraction_of_prior_nav"] <= -.01 for row in estimates)
    near, = [row for row in paths if row["pair"] == "NEAR/USDT:USDT"]
    near_diff = near["capped"]["net_usdt"] - near["uncapped"]["net_usdt"]
    output = {
        "freeze": freeze, "paths": paths, "next_bar_price_proxy_net_change_usdt": total,
        "large_wins": {"actual": old_wins, "price_proxy": new_wins},
        "large_losses": {"actual": old_losses, "price_proxy": new_losses},
        "next_step_admitted": len(estimates) == len(paths) and total > 0 and new_wins >= old_wins and new_losses < old_losses,
        "near_profit_difference_usdt": near_diff,
        "observed_account_end_nav_difference_usdt": reference["marks_and_fills"]["ending_equity_gain_usdt"],
        "additive_difference_excluding_same_near_campaign_usdt": (
            reference["marks_and_fills"]["ending_equity_gain_usdt"] - near_diff
        ),
        "orders_enabled": False,
    }
    write_once(HERE / "first_cap_exit_result.json", output)
    print(total, old_wins, new_wins, old_losses, new_losses, output["next_step_admitted"], flush=True)


if __name__ == "__main__":
    main()
