"""Inventory identifiable and missing funding-boundary clocks from actual ZIPs.

This is separate from numerical cash/NAV reconciliation. Agreement of numerical
totals does not fill absent order creation timestamps or prove venue ordering.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


HERE = Path(__file__).absolute().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def assess(name):
    freeze = read(HERE / "boundary_assessment_freeze.json")
    for path, digest in freeze["files"].items():
        assert sha(path) == digest
    plan = read(HERE / "plan.json")
    candidate = next(c for c in plan["candidates"] if c["name"] == name)
    output = Path(plan["output_root"]) / name
    receipt = read(output / "process_receipt.json")
    assert receipt["returncode"] == 0
    assert sha(output / "result.json") == receipt["result_sha256"]
    archives = list((output / "native_results").glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        assert archive.testzip() is None
        payloads = [
            json.loads(archive.read(n))
            for n in archive.namelist()
            if n.endswith(".json") and not n.endswith("_config.json")
        ]
        native = [p for p in payloads if "strategy" in p]
        assert len(native) == 1
        trades = native[0]["strategy"][candidate["strategy"]]["trades"]
    rates, input_hashes = {}, {}
    receipts = read(plan["data_receipts"]["path"])
    for pair in plan["pairs"]:
        relative = (
            "futures/" + pair.replace("/", "_").replace(":", "_") + "-1h-funding_rate.feather"
        )
        path = Path(plan["datadir"]) / relative
        digest = sha(path)
        assert digest == receipts[relative]
        frame = pd.read_feather(path)
        times = pd.DatetimeIndex(frame.date).as_unit("ms").asi8
        assert len(set(times)) == len(times)
        rates[pair] = dict(zip(times.tolist(), frame.funding_rate.tolist(), strict=True))
        input_hashes[str(path)] = digest
    events = []
    for number, trade in enumerate(trades):
        quantities = Decimal(0)
        orders = trade["orders"]
        same_times = Counter(o["order_filled_timestamp"] for o in orders)
        for index, order in enumerate(orders):
            when = int(order["order_filled_timestamp"])
            before = quantities
            amount = Decimal(str(order["amount"]))
            quantities += amount if order["ft_is_entry"] else -amount
            if when not in rates[trade["pair"]]:
                continue
            kind = (
                ("initial_entry" if index == 0 else "DCA")
                if order["ft_is_entry"]
                else ("final_exit" if index == len(orders) - 1 else "partial_exit")
            )
            creation = order.get("order_timestamp")
            problematic = kind in ("DCA", "partial_exit") or same_times[when] > 1
            unknown = problematic and creation is None
            events.append(
                {
                    "candidate": name,
                    "trade_index": number,
                    "archive_order_index": [number, index],
                    "pair": trade["pair"],
                    "timestamp_UTC": datetime.fromtimestamp(when / 1000, UTC).isoformat(),
                    "timestamp_ms": when,
                    "ft_is_entry": order["ft_is_entry"],
                    "is_short": trade["is_short"],
                    "amount": str(amount),
                    "gross_quantity_before": str(before),
                    "gross_quantity_after": str(quantities),
                    "classification": kind,
                    "same_timestamp_fills": same_times[when],
                    "source_rate": rates[trade["pair"]][when],
                    "creation_timestamp_available": creation is not None,
                    "creation_timestamp_ms": creation,
                    "native_campaign_open_timestamp_ms": trade["open_timestamp"],
                    "native_campaign_close_timestamp_ms": trade["close_timestamp"],
                    "unique_boundary_state": "UNKNOWN"
                    if unknown
                    else "NO_NONTRIVIAL_MISSING_CLOCK_FLAG",
                    "reason": "Missing creation clock for DCA/partial exit or coupled fills; numeric agreement cannot supply it"
                    if unknown
                    else "No flagged nontrivial missing-clock event; this is not venue settlement proof",
                }
            )
    unknown = [event for event in events if event["unique_boundary_state"] == "UNKNOWN"]
    result = {
        "candidate": name,
        "native_archive_sha256": sha(archives[0]),
        "audit_code_sha256": sha(__file__),
        "input_sha256": input_hashes,
        "numerical_audit_is_separate": True,
        "numerical_audit_path": str(output / "independent_account_audit.json"),
        "boundary_fill_count": len(events),
        "unknown_event_count": len(unknown),
        "unknown_campaign_count": len({e["trade_index"] for e in unknown}),
        "boundary_classification_counts": dict(Counter(e["classification"] for e in events)),
        "native_funding_boundary_identifiability": "UNKNOWN"
        if unknown
        else "NO_FLAGGED_NONTRIVIAL_MISSING_CLOCK_EVENTS",
        "unique_funding_boundary_independent_proof": False,
        "private_venue_settlement_certified": False,
        "conditional_old_execution_model": True,
        "events": events,
        "unknown_events": unknown,
    }
    path = output / "funding_boundary_identifiability.json"
    with path.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("input_sha256", "events", "unknown_events")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    assess(parser.parse_args().candidate)
