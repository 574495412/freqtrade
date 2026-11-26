"""Boundary inventory and known missing-slot gross exposure, never fee imputation."""

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd


SLOT = 1782273600000  # 2026-06-24T04:00:00Z, unresolved AXS/TRB source records.


def write_funding_receipt(stats, plan, output):
    sources = {}
    boundary = []
    missing = []
    directory = Path(plan["datadir"]) / "futures"
    for pair in plan["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        data = pd.read_feather(directory / (stem + "-1h-funding_rate.feather"))
        sources[pair] = set(pd.DatetimeIndex(data.date).as_unit("ms").asi8.tolist())
    for trade_index, trade in enumerate(stats["trades"]):
        pair = trade["pair"]
        for order_index, order in enumerate(trade["orders"]):
            when = int(order["order_filled_timestamp"])
            if when in sources[pair]:
                boundary.append(
                    {
                        "trade_index": trade_index,
                        "order_index": order_index,
                        "pair": pair,
                        "timestamp": when,
                        "is_entry": order["ft_is_entry"],
                        "amount": order["amount"],
                        "event_id": trade["enter_tag"],
                    }
                )
        if pair not in {"AXS/USDT:USDT", "TRB/USDT:USDT"}:
            continue
        if not trade["open_timestamp"] <= SLOT <= trade["close_timestamp"]:
            continue
        quantity = Decimal(0)
        same_clock = []
        for index, order in enumerate(trade["orders"]):
            amount = Decimal(str(order["amount"])) * (1 if order["ft_is_entry"] else -1)
            if order["order_filled_timestamp"] < SLOT:
                quantity += amount
            elif order["order_filled_timestamp"] == SLOT:
                same_clock.append((index, amount))
        states = [quantity]
        for _, amount in same_clock:
            quantity += amount
            states.append(quantity)
        gross = max(abs(value) for value in states)
        missing.append(
            {
                "pair": pair,
                "trade_index": trade_index,
                "event_id": trade["enter_tag"],
                "slot_timestamp": SLOT,
                "quantity_states": [str(v) for v in states],
                "gross_quantity_without_direction_netting": str(gross),
                "same_clock_order_indexes": [i for i, _ in same_clock],
                "any_gross_exposure": gross > 0,
            }
        )
    unknown = any(item["any_gross_exposure"] for item in missing)
    receipt = {
        "model": "unchanged official native inclusive funding bookkeeping",
        "known_missing_slot": "2026-06-24T04:00:00Z",
        "known_missing_pairs": ["AXS/USDT:USDT", "TRB/USDT:USDT"],
        "missing_rates_filled_with_zero": False,
        "unknown_pairs_removed": False,
        "missing_slot_exposure": missing,
        "any_missing_slot_gross_exposure": unknown,
        "complete_funding_economics": "UNKNOWN" if unknown else "INDEPENDENT_AUDIT_PENDING",
        "native_boundary_order_events": boundary,
        "independent_boundary_cash_audit_passed": False,
        "native_funding_is_not_authenticated_venue_settlement": True,
    }
    (output / "funding_boundary_inventory.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
