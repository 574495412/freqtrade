"""Reconcile the largest continuous 12-slot drawdown by native campaign."""

from collections import defaultdict
from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import trade_path
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .capacity_2024_drawdown_attribution import worst_drawdown
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .recent_two_year_attribution import ledger, reports


def attribution(wallet, manifest, peak, trough):
    dates = pd.DatetimeIndex([peak, trough]).as_unit("ns")
    clocks = dates.as_unit("ms").asi8
    marks = wallet["curve"].loc[dates]
    trades = stats_from(checked(wallet["refs"][".zip"]))["trades"]
    reader = IndependentShardRead(manifest)
    prices = {}
    rows = []
    count = np.zeros(2, dtype=int)
    gross = np.zeros(2)
    for trade in trades:
        opened, closed = trade["open_timestamp"], trade["close_timestamp"]
        if closed < clocks[0] or opened >= clocks[-1]:
            continue
        pair = trade["pair"]
        if pair not in prices:
            price = reader(pair, "5m-mark").set_index("date").open.reindex(dates).to_numpy()
            rates = reader(pair, "1h-funding_rate").set_index("date").funding_rate
            hourly = reader(pair, "1h-mark").set_index("date").open.reindex(rates.index)
            prices[pair] = price, rates.index.as_unit("ms").asi8, (hourly * rates).to_numpy()
        price, events, units = prices[pair]
        held = (clocks > opened) & (clocks <= closed)
        known = (events >= opened) & (events <= closed)
        assert np.all(np.isfinite(price[held]) & (price[held] > 0)) and np.isfinite(units[known]).all()
        contribution, exposure, _, funding, _ = trade_path(trade, clocks, np.where(held, price, 0),
                                                          events[known], units[known])
        assert isclose(funding, trade["funding_fees"], abs_tol=1e-7, rel_tol=0)
        gross += exposure
        count += held
        rows.append({"pair": pair, "side": "short" if trade["is_short"] else "long", "entry_tag": trade["enter_tag"],
                     "opening_fill": pd.Timestamp(opened, unit="ms", tz="UTC").isoformat(),
                     "held_at_peak": bool(held[0]), "held_at_trough": bool(held[1]),
                     "entry_orders_before_trough": sum(1 for order in trade["orders"]
                                                       if order["ft_is_entry"] and order["order_filled_timestamp"] < clocks[-1]),
                     "marked_pnl_change_usdt": float(contribution[1] - contribution[0])})
    np.testing.assert_array_equal(count, marks.open_trades)
    nav_change = float(marks.marked_equity.iloc[1] - marks.marked_equity.iloc[0])
    assert isclose(fsum(row["marked_pnl_change_usdt"] for row in rows), nav_change, abs_tol=3e-5, rel_tol=0)
    by_pair = defaultdict(list)
    for row in rows:
        by_pair[row["pair"]].append(row["marked_pnl_change_usdt"])
    pair_change = sorted(((pair, fsum(values)) for pair, values in by_pair.items()), key=lambda row: row[1])
    return {
        "peak": peak.isoformat(), "trough": trough.isoformat(),
        "marked_nav_usdt": marks.marked_equity.to_list(), "marked_nav_change_usdt": nav_change,
        "change_by_pair_usdt": dict(pair_change),
        "closing_ledger_change_usdt": float(marks.closed_realized_pnl.iloc[1] - marks.closed_realized_pnl.iloc[0]),
        "marked_gross_to_nav": (gross / marks.marked_equity.to_numpy()).tolist(),
        "campaigns": sorted(rows, key=lambda row: row["marked_pnl_change_usdt"]),
        "source_reads": dict(reader.calls),
    }


def main():
    source = receipt(HERE / "recent_two_year_15bp_12_verified.json")
    account = read(checked(source))["account"]
    wallet = ledger(read(checked(account["proof"])))
    plan = read(checked(account["registered_plan"]))
    peak, trough, drawdown = worst_drawdown(wallet["curve"].marked_equity)
    assert isclose(drawdown, account["periods"]["continuous"]["drawdown"], abs_tol=1e-12, rel_tol=0)
    full = attribution(wallet, plan["data_receipts"], peak, trough)
    _, reset_ref, reset_summary, _ = next(case for case in reports(15) if case[0] == "2026_nov10_to_nov25_restart")
    reset = ledger(reset_summary)["curve"]
    start = reset.index[0]
    assert peak < start < trough and trough in reset.index and reset.open_trades.iloc[0] == 0
    same_interval = attribution(wallet, plan["data_receipts"], start, trough)
    assert isclose(full["marked_nav_usdt"][1], same_interval["marked_nav_usdt"][1], abs_tol=1e-9, rel_tol=0)
    reset_to_trough = float(reset.at[trough, "marked_equity"] / reset.marked_equity.iloc[0] - 1)
    write_once(HERE / "recent_two_year_drawdown_audit.json", {
        "source": receipt(Path(__file__)), "continuous_account": source, "restart_account": reset_ref,
        "max_drawdown": drawdown, "continuous_peak_to_trough": full,
        "continuous_from_late_restart_to_trough": same_interval,
        "restart_return_through_same_trough": reset_to_trough,
        "continuous_return_from_restart_through_trough": same_interval["marked_nav_usdt"][1] / same_interval["marked_nav_usdt"][0] - 1,
        "continuous_positions_at_late_reset": int(wallet["curve"].at[start, "open_trades"]),
        "scope": "Reconstruct net marked account changes using the original fills and historical funding. "
                 "The short restart has independent capital and holds no pre-existing positions.",
        "orders_enabled": False,
    })
    print(peak, trough, drawdown, full["marked_nav_change_usdt"], full["campaigns"][0], flush=True)


if __name__ == "__main__":
    main()
