"""Trace realized and marked short risk around the observed budget crossing."""

from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .inputs import HERE
from .native_trade_path_review import books, price_reader, timestamp
from .panic_entry_audit import select_annual, select_recent
from .recent_two_year_attribution import MARK_TOLERANCE, reports


def trace(wallet, native, read_market, row):
    pair = row["pair"]
    opened = pd.Timestamp(row["first_fill"])
    crossed = pd.Timestamp(row["first_filled_budget_exceedance"])
    key = (pair, True, opened)
    trade = native[key]
    last = timestamp(trade["close_timestamp"])
    original = wallet["trades"][key]
    prior = original["equity_before_entry"]
    curve = wallet["curve"].loc[opened + FIVE_MINUTES:last]
    grid = pd.date_range(opened + FIVE_MINUTES, last, freq=FIVE_MINUTES)
    dates = curve.index.as_unit("ns")
    if not dates.equals(grid.as_unit("ns")) or not opened < crossed < last:
        raise ValueError(f"Unexpected mark range for {pair} {opened}")
    clocks = dates.insert(len(dates), last + FIVE_MINUTES).as_unit("ms").asi8
    marks, funding_events, units = read_market(pair)
    observed = marks.reindex(dates).to_numpy()
    known = (funding_events >= trade["open_timestamp"]) & (
        funding_events <= trade["close_timestamp"]
    )
    if not (np.isfinite(observed) & (observed > 0)).all() or not np.isfinite(units[known]).all():
        raise ValueError(f"Missing price or settlement for {pair} {opened}")
    pnl, exposure, _, funding, _ = independent_account_audit_v2.trade_path(
        trade, clocks, np.append(observed, 0), funding_events[known], units[known],
    )
    if not isclose(funding, trade["funding_fees"], abs_tol=1e-7, rel_tol=0):
        raise ValueError(f"Funding mismatch for {pair} {opened}")
    final = row["natural_close_net_usdt"]
    if not isclose(pnl[-1], final, abs_tol=MARK_TOLERANCE, rel_tol=0):
        raise ValueError(f"Terminal PnL mismatch for {pair} {opened}")
    if prior is None or prior <= 0:
        raise ValueError(f"Prior NAV not available for {pair} {opened}")
    before = dates <= crossed
    crossed_index = dates.get_loc(crossed)
    marks_pnl = pnl[:-1]
    breach = marks_pnl <= -.01 * prior
    gross = exposure[:-1] / curve.marked_equity.to_numpy()
    return {
        **row,
        "natural_close": last.isoformat(),
        "pre_entry_nav_usdt": prior,
        "marked_pnl_at_budget_crossing_before_fill_usdt": float(marks_pnl[crossed_index]),
        "minimum_marked_pnl_before_crossing_as_fraction_of_prior_nav": float(
            marks_pnl[before].min() / prior
        ),
        "minimum_marked_pnl_after_crossing_as_fraction_of_prior_nav": float(
            marks_pnl[~before].min() / prior
        ),
        "first_mark_loss_reaching_1pct_of_prior_nav": (
            dates[breach.argmax()].isoformat() if breach.any() else None
        ),
        "max_marked_gross_notional_over_account_nav_before_crossing": float(gross[before].max()),
        "max_marked_gross_notional_over_account_nav_after_crossing": float(gross[~before].max()),
        "checked_held_marks": len(dates),
    }


def review(account_pair, rows):
    account, plan = account_pair
    wallet, native = books(account)
    prices = price_reader(read(checked(plan)))
    mapped = [
        trace(wallet, native, prices, row)
        for row in rows if row["natural_close_net_usdt"] is not None
    ]
    short = [trade for key, trade in wallet["trades"].items() if key[1]]
    net = fsum(trade["profit"] for trade in short)
    crossing_net = fsum(row["natural_close_net_usdt"] for row in mapped)
    return {
        "native_book": wallet["refs"],
        "native_registered_plan": plan,
        "short_natural_closes": len(short),
        "short_net_realized_usdt": net,
        "budget_crossing_natural_closes": len(mapped),
        "budget_crossing_net_realized_usdt": crossing_net,
        "other_shorts_net_realized_usdt": net - crossing_net,
        "crossing_campaigns": mapped,
    }


def main():
    source = receipt(HERE / "short_rebuy_transfer_audit.json")
    audit = read(checked(source))
    freeze = read(checked(audit["freeze"]))
    annual = select_annual(read(checked(freeze["annual_15bp"]))["accounts"])
    continuous = select_recent(read(checked(freeze["continuous_15bp"]))["accounts"])
    contracts = {"2024_15bp": annual["14"], "continuous_15bp": continuous["14"]}
    name, _, _, account = next(row for row in reports(8) if row[0] == "2025")
    later, = (row for row in account["receipts"] if row["path"].endswith("/registered_plan.json"))
    contracts[f"{name}_8bp"] = (account, later)
    review_rows = {
        period: review(contract, audit["cases"][period]["14"]["crossed_campaigns"])
        for period, contract in contracts.items()
    }
    write_once(HERE / "short_rebuy_cap_path.json", {
        "source": receipt(Path(__file__)),
        "budget_screen": source,
        "by_14_slot_account": review_rows,
        "scope": (
            "Observed short paths. Marks at a recorded crossing are valued before fills at "
            "that candle. The terminal post-close observation verifies cash only. "
            "No orders or wallet paths are modified."
        ),
        "orders_enabled": False,
    })
    for period, rows in review_rows.items():
        value = rows["budget_crossing_net_realized_usdt"]
        print(f"{period}: {rows['budget_crossing_natural_closes']} crossings, {value:,.2f} USDT")


if __name__ == "__main__":
    main()
