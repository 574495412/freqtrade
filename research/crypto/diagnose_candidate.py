"""Audit native filled-order cashflows and position-management evidence."""

import argparse
import json
from itertools import groupby
from pathlib import Path

import numpy as np

from freqtrade.research.data import read, save, sha
from research.crypto.run_candidate import native_stats


def diagnose(folder: Path):
    row = read(folder / "result.json")
    stats = native_stats(folder)
    if abs(stats["profit_total_abs"] - row["net_profit_usdt"]) > 1e-5:
        raise ValueError("Native archive and summary differ")
    errors, entry_counts, exit_counts, funding = [], [], [], []
    events = []
    for index, trade in enumerate(stats["trades"]):
        flows, inventory = 0.0, 0.0
        entries = exits = 0
        direction = -1 if trade["is_short"] else 1
        for ordinal, order in enumerate(trade["orders"]):
            quantity, price = order["amount"], order["safe_price"]
            entry = order["ft_is_entry"]
            sign = -direction if entry else direction
            fee = trade["fee_open"] if entry else trade["fee_close"]
            flows += sign * quantity * price - quantity * price * fee
            inventory += quantity if entry else -quantity
            entries += int(entry)
            exits += int(not entry)
            events.append((order["order_filled_timestamp"], index, ordinal, entry, quantity, price))
        error = abs(flows + trade["funding_fees"] - trade["profit_abs"])
        if error > 1e-5 or abs(inventory) > max(1e-7, trade["amount"] * 1e-9):
            raise ValueError("Native filled-order cashflow or terminal inventory mismatch")
        errors.append(error)
        entry_counts.append(entries)
        exit_counts.append(exits)
        funding.append(trade["funding_fees"])
    positions, peak_margin = {}, 0.0
    for _, group in groupby(sorted(events), key=lambda event: event[0]):
        for _, index, _, entry, quantity, price in group:
            held, basis = positions.get(index, (0.0, 0.0))
            if entry:
                held, basis = held + quantity, basis + quantity * price
            else:
                if held <= 0 or quantity > held * (1 + 1e-9):
                    raise ValueError("Native exit preceded its inventory")
                basis *= max(0.0, 1 - quantity / held)
                held = max(0.0, held - quantity)
            positions[index] = held, basis
        margin = sum(b / stats["trades"][i]["leverage"] for i, (_, b) in positions.items())
        peak_margin = max(peak_margin, margin)
    durations = [t["trade_duration"] / 1440 for t in stats["trades"]]
    return {
        "result_sha256": sha(folder / "result.json"),
        "closed_trades": len(stats["trades"]),
        "maximum_trade_cashflow_reconciliation_error_usdt": max(errors, default=0.0),
        "trades_with_additional_entries": sum(n > 1 for n in entry_counts),
        "maximum_entries_in_one_trade": max(entry_counts, default=0),
        "trades_with_partial_exits": sum(n > 1 for n in exit_counts),
        "maximum_exits_in_one_trade": max(exit_counts, default=0),
        "median_holding_days": float(np.median(durations)) if durations else None,
        "maximum_holding_days": max(durations, default=None),
        "maximum_end_of_fill_timestamp_entry_basis_margin_usdt": peak_margin,
        "margin_convention": (
            "entry basis after each timestamp; excludes fees, funding, PnL and intrabar fill order"
        ),
        "force_exit_trades": sum(t["exit_reason"] == "force_exit" for t in stats["trades"]),
        "funding_net_usdt": sum(funding),
        "orders_enabled": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True, type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    target = folder / "filled_order_audit.json"
    if target.exists():
        raise ValueError("Audit exists; preserve immutable receipts")
    result = diagnose(folder)
    save(target, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
