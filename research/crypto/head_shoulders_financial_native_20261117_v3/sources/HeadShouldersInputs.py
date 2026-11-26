"""Read-only original source bridge and event identities, never outcome selection."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


SOURCE_LOADER_SHA = "87a7be64d338bdd5fc01dd3d6b4660f93edfb9d5588d3eba12a0dfd5ae136013"
SOURCE_COMMIT = "da99c20bf3d977b639451258cd6cfca9baa1dcc3"
ORIGIN = pd.Timestamp("2023-08-01T00:00:00Z")
NATIVE_START = pd.Timestamp("2024-12-31T23:00:00Z")
OHLCV = ["open", "high", "low", "close", "volume"]


def original_source(config):
    path = Path(config["research_head_shoulders_source_loader"])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_LOADER_SHA:
        raise ValueError("Original v1 source loader hash differs")
    name = "_fixed_head_shoulders_v1_source"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    author, _ = module.load_author()
    return module, author


def assembled_history(dataframe, pair, config, source):
    record = config["research_head_shoulders_history"][pair]
    if set(record) != {"path", "sha256"}:
        raise ValueError("Exactly path and sha256 are required for frozen history")
    path = Path(record["path"])
    if not path.is_absolute():
        raise ValueError("Frozen history path must be absolute")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("Frozen input hash differs")
    history = pd.read_feather(io.BytesIO(raw))
    source.validate_frame(history)
    source.validate_frame(dataframe)
    if history.date.iloc[0] != ORIGIN or dataframe.date.iloc[0] < NATIVE_START:
        raise ValueError("Frozen history origin or native start differs")
    indexed = history.set_index("date")
    if not dataframe.date.isin(indexed.index).all():
        raise ValueError("Native timestamp outside frozen history")
    if not np.array_equal(
        indexed.loc[dataframe.date, OHLCV].to_numpy(dtype=float),
        dataframe[OHLCV].to_numpy(dtype=float),
    ):
        raise ValueError("Native OHLCV mismatch")
    before = history.loc[history.date < dataframe.date.iloc[0], ["date", *OHLCV]]
    combined = pd.concat([before, dataframe[["date", *OHLCV]]], ignore_index=True)
    source.validate_frame(combined)
    if combined.date.iloc[-1] != dataframe.date.iloc[-1]:
        raise ValueError("Future history was prepended")
    return combined, len(before)


def identity(pair, row):
    # Full immutable geometric record; no return or future outcome fields.
    payload = {"source_commit": SOURCE_COMMIT, "pair": pair, **row}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return "hs1_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def event_records(frame, pair, source, author):
    events = source.extract_events(frame, author)
    records = []
    for row in events.to_dict("records"):
        event = {**row, "event_id": identity(pair, row), "pair": pair}
        event["side"] = "long" if row["inverted"] else "short"
        event["expiry_bar_open"] = ORIGIN + pd.Timedelta(hours=row["source_expiry_i"])
        event["expiry_available"] = event["expiry_bar_open"] + pd.Timedelta(hours=1)
        event["entry_available"] = row["earliest_signal_available"]
        records.append(event)
    if len({e["event_id"] for e in records}) != len(records):
        raise ValueError("Source event ID collision")
    if len({e["break_i"] for e in records}) != len(records):
        raise ValueError("Ambiguous simultaneous same-pair source events")
    return records


def projected_indicators(dataframe, records):
    """Only confirmation rows carry source events; no forward-fill/re-entry state."""
    dataframe["hs_event_id"] = ""
    dataframe["hs_direction"] = 0
    dataframe["hs_self_terminal"] = False
    positions = {date: index for index, date in enumerate(dataframe.date)}
    for event in records:
        position = positions.get(event["confirmation_bar_open"])
        if position is None:
            continue
        for name, value in [
            ("hs_event_id", event["event_id"]),
            ("hs_direction", 1 if event["inverted"] else -1),
            ("hs_self_terminal", event["self_terminal_at_confirmation"]),
        ]:
            dataframe.iloc[position, dataframe.columns.get_loc(name)] = value
    return dataframe
