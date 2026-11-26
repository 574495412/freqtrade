"""Read-only source event, callback and native trade binding receipts."""

import gzip
import json
from collections import Counter

import pandas as pd


def write_policy_receipts(strategy, output, stats=None):
    records = getattr(strategy, "_pair_events", {})
    errors = getattr(strategy, "callback_errors", [])
    audit = getattr(strategy, "policy_audit", [])
    with gzip.open(output / "source_event_catalog.json.gz", "wt") as stream:
        json.dump(records, stream, default=str)
    with gzip.open(output / "source_policy_audit.json.gz", "wt") as stream:
        json.dump(audit, stream, default=str)
    bindings = []
    if stats is not None:
        events = getattr(strategy, "_events", {})
        seen = set()
        allowed_exits = {
            "hs_source_target",
            "hs_source_stop",
            "hs_source_expiry",
            "stop_loss",
            "liquidation",
            "force_exit",
        }
        for trade in stats["trades"]:
            tag = trade["enter_tag"]
            event = events.get(tag)
            if (
                event is None
                or event["pair"] != trade["pair"]
                or event["inverted"] == trade["is_short"]
                or event["self_terminal_at_confirmation"]
                or tag in seen
            ):
                raise ValueError("Native trade does not bind to one executable original event")
            if trade["open_timestamp"] != int(
                pd.Timestamp(event["entry_available"]).timestamp() * 1000
            ):
                raise ValueError("Native entry occurred outside its fixed initial clock")
            if trade["exit_reason"] not in allowed_exits:
                raise ValueError("Unregistered exit reason")
            seen.add(tag)
            bindings.append(
                {
                    "pair": trade["pair"],
                    "event_id": tag,
                    "open_timestamp": trade["open_timestamp"],
                    "close_timestamp": trade["close_timestamp"],
                    "exit_reason": trade["exit_reason"],
                }
            )
    receipt = {
        "callback_errors": errors,
        "callback_error_count": len(errors),
        "source_events": sum(len(events) for events in records.values()),
        "self_terminal_event_ids": [
            e["event_id"]
            for events in records.values()
            for e in events
            if e["self_terminal_at_confirmation"]
        ],
        "policy_audit_status_counts": dict(Counter(row["status"] for row in audit)),
        "native_trade_event_bindings": bindings,
        "read_only_receipt_not_an_independent_account_audit": True,
    }
    (output / "source_policy_receipt.json").write_text(
        json.dumps(receipt, indent=2, default=str) + "\n"
    )
    return receipt


def reject_callback_errors(strategy, output):
    errors = getattr(strategy, "callback_errors", [])
    if errors:
        write_policy_receipts(strategy, output)
        raise ValueError("Fatal source callback errors: " + json.dumps(errors, default=str))
