"""Replay publisher BTC anchors and compare normalized DYDX/DOT to native update identities."""

import json
import sys
from collections import Counter
from decimal import Decimal
from itertools import groupby, pairwise
from operator import itemgetter
from statistics import median

import pyarrow.parquet as pq
from capture import HERE, RAW, ROOT, SAMPLE_DAY, TARGETS, sha, utc, write


sys.path.insert(0, str(HERE.parent / "historical_universe_depth_20261106"))

from audit_universe import CAPTURE_RESULT, Book, nanoseconds, timeline


SECOND = 10**9
MILLISECOND = 10**6
BAND = Decimal("0.002")
FRESH = 15 * SECOND


def case_start(day):
    return nanoseconds(day + "T00:00:00Z")


def bands(bids, asks):
    bid, ask = max(bids), min(asks)
    if bid >= ask:
        raise ValueError("crossed anchor")
    return (
        (bid - min(bids)) / bid * 10_000,
        (max(asks) - ask) / ask * 10_000,
    )


def candlefeed_groups(path, *, start, snapshot):
    fields = [
        "node",
        "symbol",
        "event_time",
        "tx_time",
        "first_update_id",
        "final_update_id",
        "prev_final_update_id",
        "recv_time",
        "is_snapshot",
        "side",
        "price",
        "qty",
    ]
    fields.append("anchor_class" if snapshot else "segment")
    table = pq.read_table(
        path,
        columns=fields,
        filters=[
            ("event_time", ">=", start // MILLISECOND),
            ("event_time", "<", (start + 3600 * SECOND) // MILLISECOND),
        ],
    )
    cols = table.to_pydict()
    rows = zip(*(cols[field] for field in fields), strict=True)
    # One WS message or one received REST snapshot. Each stored level carries the full key.
    key_fields = itemgetter(0, 1, 2, 3, 4, 5, 6, 7, 8, 12)
    for header, rows_at_event in groupby(rows, key=key_fields):
        node, symbol, event, transaction, first, final, previous, recv, is_snapshot, flag = header
        if symbol != "BTCUSDT" or is_snapshot is not snapshot:
            raise ValueError("unexpected symbol or frame kind")
        if not 0 < transaction <= event <= recv // MILLISECOND or not 0 < first <= final:
            raise ValueError("invalid CandleFeed clock or sequence")
        sides = {"bid": [], "ask": []}
        for row in rows_at_event:
            side, price, qty = row[9:12]
            sides[side].append((price, qty))
        yield {
            "node": node,
            "E": event,
            "T": transaction,
            "U": first,
            "u": final,
            "pu": previous,
            "recv": recv,
            "flag": flag,
            "b": sides["bid"],
            "a": sides["ask"],
        }


def observed_book(anchor, events, cutoff):
    book = Book("futures")
    book.snapshot({"lastUpdateId": anchor["u"], "bids": anchor["b"], "asks": anchor["a"]})
    last_receipt, last_frame = None, None
    for frame in events:
        if frame["u"] < anchor["u"]:
            continue
        if frame["recv"] > cutoff:
            # Future-received frame followed by a frame already received implies ambiguous coverage.
            if any(later["u"] > frame["u"] and later["recv"] <= cutoff for later in events):
                raise ValueError("out-of-order receipt straddles cutoff")
            if last_frame is not None and frame["pu"] != last_frame["u"]:
                raise ValueError("sequence break could straddle cutoff")
            break
        if frame["node"] != anchor["node"]:
            raise ValueError("anchor and deltas use different node clocks")
        if last_receipt is not None and frame["recv"] < last_receipt:
            raise ValueError("delta receipt order regressed")
        if not book.ready and frame["pu"] == book.last < frame["U"]:
            book.ready = True  # Exact predecessor is independently supplied by Binance.
        if book.update(frame):
            last_receipt, last_frame = frame["recv"], frame
    if (
        last_frame is None
        or cutoff - last_frame["recv"] > FRESH
        or cutoff - last_frame["E"] * MILLISECOND > FRESH
    ):
        raise ValueError("no fresh causal delta after snapshot")
    observation = book.observation()
    if observation is None or not observation["bbo_within_known_snapshot_region"]:
        raise ValueError("no reconstructed observation")
    return {
        "anchor_id": anchor["u"],
        "anchor_received_ns": anchor["recv"],
        "anchor_tx_ms": anchor["T"],
        "anchor_class": anchor["flag"],
        "anchor_bid_boundary": str(book.bid_boundary),
        "anchor_ask_boundary": str(book.ask_boundary),
        "delta_event_ms": last_frame["E"],
        "delta_received_ns": last_frame["recv"],
        "node": anchor["node"],
        "applied": book.applied,
        **observation,
    }


def candlefeed():
    start = case_start(SAMPLE_DAY)
    anchors = list(candlefeed_groups(RAW / "btc-snapshot.parquet", start=start, snapshot=True))
    frames = list(candlefeed_groups(RAW / "btc-depth-00.parquet", start=start, snapshot=False))
    if not anchors or not frames:
        raise ValueError("empty registered CandleFeed sample")
    gaps = [
        {"after": prior["u"], "before": frame["u"], "segment": frame["flag"]}
        for prior, frame in pairwise(frames)
        if not prior["u"] < frame["u"]
        or frame["pu"] != prior["u"]
        or frame["flag"] != prior["flag"]
    ]
    spans = []
    for anchor in anchors:
        if (
            anchor["U"] != anchor["u"]
            or anchor["pu"] != 0
            or not 0 < len(anchor["b"]) <= 1000
            or not 0 < len(anchor["a"]) <= 1000
        ):
            raise ValueError("invalid native snapshot identity or depth")
        bid_bps, ask_bps = bands(dict(anchor["b"]), dict(anchor["a"]))
        spans.append(
            {
                "anchor_id": anchor["u"],
                "received_ns": anchor["recv"],
                "node": anchor["node"],
                "anchor_class": anchor["flag"],
                "bid_levels": len(anchor["b"]),
                "ask_levels": len(anchor["a"]),
                "bid_band_bps": str(bid_bps),
                "ask_band_bps": str(ask_bps),
                "both_cover_20bps": min(bid_bps, ask_bps) >= 20,
            }
        )

    observations = []
    for minute in range(5, 60, 5):
        cutoff = start + minute * 60 * SECOND
        options = [item for item in anchors if item["recv"] <= cutoff and item["flag"] != "in_gap"]
        row = {"cutoff_ns": cutoff}
        try:
            if not options:
                raise ValueError("no received native anchor")
            anchor = max(options, key=itemgetter("recv"))
            row["book"] = observed_book(anchor, frames, cutoff)
        except ValueError as exc:
            row["error"] = str(exc)
        observations.append(row)
    return {
        "day": SAMPLE_DAY,
        "frozen_window": None,
        "snapshot_frames": spans,
        "delta_frames": len(frames),
        "delta_rows": sum(len(f["a"]) + len(f["b"]) for f in frames),
        "node_counts": dict(Counter(frame["node"] for frame in frames)),
        "sequence_breaks": gaps,
        "observations": observations,
    }


def native_frames(symbol, day):
    receipts = json.loads(CAPTURE_RESULT.read_text())["receipts"]
    record = next(
        (
            row
            for row in receipts
            if row["market"] == "futures"
            and row["day"] == day
            and row["kind"] == "depth"
            and symbol.lower() in row["symbols"]
        ),
        None,
    )
    if record is None:
        return {}, []
    path = ROOT / record["path"]
    if sha(path.read_bytes()) != record["sha256"]:
        raise ValueError("previous native capture changed")
    events = {}
    for _, receive, parsed in timeline(record):
        if parsed is None:
            continue
        name, channel, message = parsed
        if name != symbol.lower() or not channel.startswith("depth@"):
            continue
        data = message["data"]
        identity = data["u"]
        if identity in events:
            raise ValueError("duplicate sequence in native comparison source")
        levels = {
            (side, Decimal(price)): Decimal(qty)
            for side, column in ((1, "b"), (2, "a"))
            for price, qty in data[column]
        }
        events[identity] = (data["E"] * MILLISECOND, data["T"] * MILLISECOND, receive, levels)
    return events, [{"path": record["path"], "sha256": record["sha256"]}]


def hf_sample(symbol, day):
    start = case_start(day)
    path = RAW / f"hf-{symbol}-{day}.parquet"
    table = pq.read_table(
        path, filters=[("ts_init", ">=", start), ("ts_init", "<", start + 3600 * SECOND)]
    )
    fields = (
        "sequence",
        "ts_event",
        "ts_init",
        "action",
        "side",
        "price_raw",
        "price_prec",
        "size_raw",
        "size_prec",
        "flags",
    )
    cols = table.to_pydict()
    native, reference = native_frames(symbol, day)
    matches, amounts, samples = Counter(), Counter(), []
    raw_ids = set()
    transaction_offsets, receipt_offsets = [], []
    for (identity, event_ns, received_ns), group in groupby(
        zip(*(cols[field] for field in fields), strict=True), key=itemgetter(0, 1, 2)
    ):
        group = list(group)
        if group[0][-1] & 32 and group[0][3] in (4, 5):
            matches["snapshot_bursts"] += 1
            amounts["snapshot_levels"] += len(group) - 1
            continue
        if any(row[3] in (4, 5) for row in group):
            matches["control_or_other_clear"] += 1
            continue
        matches["normalized_delta_frames"] += 1
        matches["event_after_receipt"] += event_ns > received_ns
        if identity in raw_ids:
            matches["duplicate_delta_ids"] += 1
        raw_ids.add(identity)
        if identity not in native:
            continue
        matches["same_native_u"] += 1
        expected_event, expected_transaction, native_recv, expected_levels = native[identity]
        matches["same_native_E"] += event_ns == expected_event
        matches["same_native_T"] += event_ns == expected_transaction
        matches["same_receipt_clock"] += received_ns == native_recv
        transaction_offsets.append(event_ns - expected_transaction)
        receipt_offsets.append(received_ns - native_recv)
        observed = {
            (side, Decimal(price).scaleb(-price_prec)): Decimal(qty).scaleb(-qty_prec)
            for _, _, _, _, side, price, price_prec, qty, qty_prec, _ in group
        }
        if len(observed) != len(group):
            matches["normalized_duplicate_prices"] += 1
        if observed == expected_levels and len(observed) == len(group):
            matches["exact_frame_levels"] += 1
        else:
            matches["frame_level_conflicts"] += 1
            amounts["native_levels_missing"] += len(expected_levels.keys() - observed.keys())
            amounts["normalized_levels_extra"] += len(observed.keys() - expected_levels.keys())
            amounts["same_level_quantity_conflicts"] += sum(
                observed[key] != expected_levels[key]
                for key in observed.keys() & expected_levels.keys()
            )
            if len(samples) < 5:
                samples.append(
                    {
                        "id": identity,
                        "native_levels": len(expected_levels),
                        "normalized_levels": len(observed),
                    }
                )
    missing = {"first_update_id", "prev_final_update_id"} - set(table.column_names)
    offsets = {}
    for key, values in (
        ("normalized_ts_event_minus_native_T", transaction_offsets),
        ("normalized_ts_init_minus_native_receipt", receipt_offsets),
    ):
        if values:
            offsets[key] = {
                "min_ns": min(values),
                "median_ns": median(values),
                "max_ns": max(values),
            }
    return {
        "symbol": symbol,
        "day": day,
        "day_rows": pq.ParquetFile(path).metadata.num_rows,
        "hour_rows": table.num_rows,
        "columns": table.column_names,
        "native_reference": reference,
        "native_reference_frames": len(native),
        "frame_counts": dict(matches),
        "level_counts": dict(amounts),
        "matched_clock_offsets": offsets,
        "level_conflict_examples": samples,
        "missing_raw_continuity_fields": sorted(missing),
        "original_rest_anchor_boundary_verified": False,
        "eligible": False,
    }


def run():
    reg = HERE / "registration.json"
    capture = HERE / "capture_receipts.json"
    receipt = json.loads(capture.read_text())
    if receipt["registration_sha256"] != sha(reg.read_bytes()):
        raise ValueError("capture registration changed")
    for row in receipt["receipts"]:
        if row.get("path") and sha((ROOT / row["path"]).read_bytes()) != row["sha256"]:
            raise ValueError("retained source changed")
    result = {
        "observed_at_utc": utc(),
        "capture_receipts_sha256": sha(capture.read_bytes()),
        "audit_code_sha256": sha((HERE / "audit.py").read_bytes()),
        "candlefeed": candlefeed(),
        "normalized_archive": [hf_sample(*item) for item in TARGETS],
        "new_frozen_window_depth_admissions": 0,
        "native_final_kline_receipts_in_frozen_windows": 0,
        "financial_accounts": 0,
    }
    write(HERE / "result.json", result)


if __name__ == "__main__":
    run()
