"""Reconcile native paper funding with official event history and filled quantities."""

import argparse
import math
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def timestamp(value):
    clock = pd.Timestamp(value)
    clock = clock.tz_localize("UTC") if clock.tzinfo is None else clock
    return clock.value // 1_000_000


def campaign_funding(trade, orders, events, end):
    cash, count = 0.0, 0
    for event in sorted(events, key=lambda x: x["fundingTime"]):
        clock = int(event["fundingTime"])
        if not timestamp(trade["open_date"]) <= clock <= end:
            continue
        quantity = 0.0
        for order in orders:
            if not order["filled"] or order["order_filled_date"] is None:
                continue
            when = timestamp(order["order_filled_date"])
            if when == clock:
                raise ValueError("Fill and funding share an unresolved timestamp")
            if when < clock:
                entry = order["ft_order_side"] == ("sell" if trade["is_short"] else "buy")
                quantity += float(order["filled"]) * (1 if entry else -1)
        if quantity < -1e-8:
            raise ValueError("Funding reconstruction has negative owned inventory")
        cash += (
            (1 if trade["is_short"] else -1)
            * max(0, quantity)
            * (float(event["markPrice"]) * float(event["fundingRate"]))
        )
        count += 1
    if not math.isfinite(cash):
        raise ValueError("Nonfinite funding cash")
    return cash, count


def audit(snapshot, funding, output):
    if output.exists():
        raise ValueError("Preserve earlier audits")
    nav, history, rows = read(snapshot / "snapshot.json"), read(funding), []
    for component in nav["components"]:
        ledger = read(snapshot / (component["component"] + "_ledger.json"))
        values = {x["trade_id"]: x for x in component["valuations"]}
        for trade in ledger["trades"]:
            if not trade["is_open"]:
                raise ValueError("This current probe covers open campaigns only")
            symbol = trade["pair"].split("/")[0] + "USDT"
            series = next(x for x in history["series"] if x["symbol"] == symbol)
            end = int(values[trade["id"]]["mark_time_ms"])
            if (
                history["requested_start_ms"] > timestamp(trade["open_date"])
                or history["requested_end_ms"] < end
            ):
                raise ValueError("Official event request does not cover the campaign")
            if len(series["events"]) >= history["limit"]:
                raise ValueError("Potentially truncated funding history")
            clocks = [x["fundingTime"] for x in series["events"]]
            if len(set(clocks)) != len(clocks):
                raise ValueError("Duplicate settlement events")
            orders = [x for x in ledger["orders"] if x["ft_trade_id"] == trade["id"]]
            cash, count = campaign_funding(trade, orders, series["events"], end)
            error = abs(cash - float(trade["funding_fees"] or 0))
            if error > 1e-7:
                raise ValueError("Native accrued funding differs from official event cash")
            rows.append(
                {
                    "component": component["component"],
                    "trade_id": trade["id"],
                    "pair": trade["pair"],
                    "events_since_open": count,
                    "reconstructed_funding_usdt": cash,
                    "error_usdt": error,
                }
            )
    result = {
        "passed": True,
        "campaigns": rows,
        "snapshot_sha256": sha(snapshot / "snapshot.json"),
        "official_history_sha256": sha(funding),
        "scope": "Current open paper campaigns only; no closed-profit qualification",
    }
    save(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--funding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(audit(args.snapshot, args.funding, args.output))
