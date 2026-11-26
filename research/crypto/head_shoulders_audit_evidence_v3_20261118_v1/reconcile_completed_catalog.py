"""Compare actual source events with the original catalog and its derived labels."""

import gzip
import json
from pathlib import Path

import pandas as pd

from .independent_account_audit_v3 import ms, read, verified


def source_catalog(directory, protocol):
    reference = pd.read_parquet(verified(protocol["source_catalog"]))
    with gzip.open(directory / "source_event_catalog.json.gz", "rt") as stream:
        actual = json.load(stream)
    derived = {"pair_order", "policy_event_status", "native_location"}
    date_fields = {
        "confirmation_bar_open", "earliest_signal_available", "expiry_bar_open",
        "expiry_available", "entry_available",
    }
    expected_pairs = reference[["pair", "pair_order"]].drop_duplicates().sort_values("pair_order")
    if list(actual) != expected_pairs.pair.tolist():
        raise ValueError("Source catalog pair order differs")
    assert expected_pairs.pair_order.tolist() == list(range(40))
    index_by_pair = dict(zip(expected_pairs.pair, expected_pairs.pair_order, strict=True))
    mapping = {e["event_id"]: e for items in actual.values() for e in items}
    assert len(mapping) == sum(map(len, actual.values())) == len(reference) == 1891
    assert all(e["pair"] == pair for pair, events in actual.items() for e in events)
    execution_start = ms("2024-12-31T23:00:00Z")
    execution_end = ms("2026-08-31T23:00:00Z")
    for ref in reference.to_dict("records"):
        event = mapping[ref["event_id"]]
        assert set(ref) == set(event) | derived
        assert not derived.intersection(event)
        if ms(event["confirmation_bar_open"]) < execution_start:
            location = "COLD_HISTORY_ONLY"
        elif ms(event["entry_available"]) > execution_end:
            location = "FINAL_BAR_NO_NEXT_NATIVE_ROW"
        else:
            location = "CONFIRMATION_ROW_AVAILABLE"
        status = "MASKED_SELF_TERMINAL_KNOWN_FLAT" if event["self_terminal_at_confirmation"] else location
        assert ref["policy_event_status"] == status and ref["native_location"] == location
        assert ref["pair_order"] == index_by_pair[event["pair"]]
        for name, value in event.items():
            expected = ref[name]
            assert (ms(value) == ms(expected) if name in date_fields else value == expected), (
                ref["event_id"], name,
            )
    receipt = read(directory / "source_policy_receipt.json")
    assert receipt["callback_errors"] == [] and receipt["callback_error_count"] == 0
    terminals = {e["event_id"] for e in mapping.values() if e["self_terminal_at_confirmation"]}
    assert len(terminals) == 30 and terminals == set(receipt["self_terminal_event_ids"])
    with gzip.open(directory / "source_policy_audit.json.gz", "rt") as stream:
        return mapping, json.load(stream)
