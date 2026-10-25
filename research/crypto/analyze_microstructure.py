"""Inspect received data and causal factors without forward-return labels."""

import argparse
import gzip
import json
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.microstructure_features import FACTOR_NAMES, FeatureBuilder


def age_bounds(record, message, samples):
    prior = [s for s in samples if s["received_monotonic_ns"] <= record["received_monotonic_ns"]]
    if not prior:
        return None
    sample = prior[-1]
    server = sample["server_time_ms"]
    received = record["received_wall_ns"] / 1e6
    lower = server - sample["received_wall_ns"] / 1e6 - 1
    upper = server - sample["sent_wall_ns"] / 1e6 + 1
    return received + lower - message["E"], received + upper - message["E"]


def decimal_snapshot(books, trades, row):
    now = row.decision_monotonic_ns
    book = next(data for time, data in reversed(books) if time <= now)
    with localcontext() as context:
        context.prec = 40
        bids = [[Decimal(x) for x in pair] for pair in book["b"]]
        asks = [[Decimal(x) for x in pair] for pair in book["a"]]
        bid, q_bid = bids[0]
        ask, q_ask = asks[0]
        mid = (bid + ask) / 2
        result = {
            "spread_bps": 10_000 * (ask - bid) / mid,
            "imbalance_l1": (q_bid - q_ask) / (q_bid + q_ask),
            "microprice_deviation_bps": 10_000
            * ((ask * q_bid + bid * q_ask) / (q_bid + q_ask) / mid - 1),
        }
        for count in [5, 20]:
            b = sum(p * q for p, q in bids[:count])
            a = sum(p * q for p, q in asks[:count])
            result[f"imbalance_l{count}"] = (b - a) / (b + a)
        for seconds in [30, 120]:
            chosen = [data for time, data in trades if now - seconds * 1_000_000_000 < time <= now]
            normal = sum(Decimal(t["p"]) * Decimal(t["nq"]) for t in chosen)
            signed = sum((-1 if t["m"] else 1) * Decimal(t["p"]) * Decimal(t["nq"]) for t in chosen)
            result[f"received_flow_{seconds}s"] = signed / normal
            if seconds == 30:
                total = sum(Decimal(t["p"]) * Decimal(t["q"]) for t in chosen)
                total_signed = sum(
                    (-1 if t["m"] else 1) * Decimal(t["p"]) * Decimal(t["q"]) for t in chosen
                )
                result["received_total_flow_30s"] = total_signed / total
                result["received_rpi_share_30s"] = (total - normal) / total
        signs = []
        for offset in range(6):
            end = now - offset * 5_000_000_000
            chosen = [t for time, t in trades if end - 5_000_000_000 < time <= end]
            signed = sum((-1 if t["m"] else 1) * Decimal(t["p"]) * Decimal(t["nq"]) for t in chosen)
            signs.append(Decimal(1) if signed > 0 else Decimal(-1) if signed < 0 else Decimal(0))
        result["persistence_30s"] = sum(signs) / 6
        result["flow_x_imbalance"] = result["received_flow_30s"] * result["imbalance_l5"]
        result["flow_x_microprice"] = (
            result["received_flow_30s"] * result["microprice_deviation_bps"]
        )
        return {key: float(value) for key, value in result.items()}


def analyze(capture_path, quality_path, output):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve prior source analysis")
    for name, expected in read(capture_path / "receipts.json").items():
        if sha(capture_path / name) != expected:
            raise ValueError(f"Captured input changed: {name}")
    plan, quality = read(capture_path / "plan.json"), read(quality_path)
    status = read(capture_path / "status.json")
    if status["state"] != "terminal":
        raise ValueError("Only analyze a closed registered capture")
    output.mkdir(parents=True)
    (output / "sources").mkdir()
    for filename in ["microstructure_features.py", "analyze_microstructure.py"]:
        source = Path(__file__).with_name(filename)
        (output / "sources" / filename).write_bytes(source.read_bytes())
    save(output / "quality_plan.json", quality)
    save(
        output / "input_freeze.json",
        {
            "capture_receipts_sha256": sha(capture_path / "receipts.json"),
            "quality_plan_sha256": sha(quality_path),
            "source_sha256": {p.name: sha(p) for p in (output / "sources").iterdir()},
            "forward_return_labels_computed": False,
            "orders_enabled": False,
        },
    )
    records = []
    for chunk in read(capture_path / "chunks.json"):
        with gzip.open(capture_path / chunk["file"], "rt") as stream:
            records.extend(json.loads(line) for line in stream)
    if len(records) != status["raw_rows"]:
        raise ValueError("Captured row total differs from raw records")
    events = [json.loads(line) for line in (capture_path / "events.jsonl").read_text().splitlines()]
    ended = next(
        event["monotonic_ns"] for event in reversed(events) if event["event"] == "capture_ended"
    )
    builder = FeatureBuilder(plan)
    clocks = read(capture_path / "clock_samples.json")
    ages = {
        f"{channel}:{symbol}": [] for channel in ["depth", "trades"] for symbol in plan["symbols"]
    }
    raw_books = {symbol: [] for symbol in plan["symbols"]}
    raw_trades = {symbol: [] for symbol in plan["symbols"]}
    for record in records:
        builder.ingest(record)
        data = json.loads(record["raw"])["data"]
        symbol, channel = data["s"], record["channel"]
        bounds = age_bounds(record, data, clocks)
        if bounds:
            ages[f"{channel}:{symbol}"].append(bounds)
        (raw_books if channel == "depth" else raw_trades)[symbol].append(
            (record["received_monotonic_ns"], data)
        )
    frame = pd.DataFrame(builder.finish(ended))
    frame.to_parquet(output / "features.parquet", index=False)
    prefix_records = records[: int(len(records) * 0.8)]
    prefix_builder = FeatureBuilder(plan)
    for record in prefix_records:
        prefix_builder.ingest(record)
    prefix = pd.DataFrame(prefix_builder.finish(prefix_records[-1]["received_monotonic_ns"]))
    pd.testing.assert_frame_equal(prefix, frame.iloc[: len(prefix)].reset_index(drop=True))
    rows, failures = [], []
    for symbol, subset in frame.groupby("symbol", sort=False):
        warmed = subset.loc[subset.decision_monotonic_ns.ge(builder.anchor_mono + 120_000_000_000)]
        complete = warmed.loc[warmed.complete_factors]
        fraction = len(complete) / len(warmed) if len(warmed) else 0
        rows.append(
            {
                "symbol": symbol,
                "post_warmup_rows": len(warmed),
                "complete_rows": len(complete),
                "complete_fraction": fraction,
                "median_spread_bps": float(complete.spread_bps.median()) if len(complete) else None,
            }
        )
        if (
            len(complete) < quality["minimum_post_warmup_complete_rows_per_symbol"]
            or fraction < quality["minimum_post_warmup_complete_fraction"]
        ):
            failures.append(f"insufficient_causal_feature_coverage:{symbol}")
    clock_rtts = [(c["received_monotonic_ns"] - c["sent_monotonic_ns"]) / 1e6 for c in clocks]
    if not clock_rtts or max(clock_rtts) > quality["maximum_clock_round_trip_ms"]:
        failures.append("clock_probe_uncertainty")
    latency = {}
    for key, bounds in ages.items():
        total = builder.counts[key]
        fraction = len(bounds) / total if total else 0
        upper = np.asarray(bounds)[:, 1] if bounds else np.array([])
        latency[key] = {
            "observations": total,
            "clock_bounded": len(bounds),
            "clock_coverage_fraction": fraction,
            "median_upper_bound_ms": float(np.median(upper)) if len(upper) else None,
            "p99_upper_bound_ms": float(np.quantile(upper, 0.99)) if len(upper) else None,
            "entire_age_interval_negative": int((upper < 0).sum()),
        }
        if (
            fraction < quality["minimum_event_age_clock_coverage_fraction"]
            or not len(upper)
            or np.quantile(upper, 0.99) > quality["maximum_p99_event_to_receive_upper_bound_ms"]
            or (upper < 0).any()
        ):
            failures.append(f"source_clock_or_delay:{key}")
    disallowed = [
        "invalid_book:",
        "trade_id_gap:",
        "conflicting_or_reordered_trade:",
        "book_nonincreasing_id:",
        "wall_clock_step_records",
    ]
    if any(
        count > 0 and any(key.startswith(prefix) for prefix in disallowed)
        for key, count in builder.counts.items()
    ):
        failures.append("source_schema_clock_or_sequence_anomaly")
    if status["stop_reason"] != "registered_duration_completed" or status["error_type"]:
        failures.append("capture_did_not_complete_registered_duration")
    if any(value != 1 for value in status["epochs"].values()):
        failures.append("reconnection_observed")
    errors = []
    for symbol in plan["symbols"]:
        eligible = frame.loc[frame.symbol.eq(symbol) & frame.complete_factors]
        if len(eligible):
            sample = eligible.sample(min(len(eligible), 10), random_state=20261024)
            for row in sample.itertuples():
                expected = decimal_snapshot(raw_books[symbol], raw_trades[symbol], row)
                errors.extend(abs(expected[name] - getattr(row, name)) for name in FACTOR_NAMES)
    maximum_error = max(errors) if errors else None
    if maximum_error is None or maximum_error > quality["sampled_decimal_feature_error_tolerance"]:
        failures.append("independent_feature_arithmetic")
    result = {
        "source_ready_for_longer_forward_capture": not failures,
        "failure_reasons": failures,
        "capture_rows": len(records),
        "feature_rows": len(frame),
        "symbols": rows,
        "source_counts": dict(builder.counts),
        "event_to_receive_interval_estimates": latency,
        "clock_round_trips_ms": clock_rtts,
        "actual_prefix_rows_equal": len(prefix),
        "independent_decimal_feature_checks": len(errors),
        "maximum_feature_error": maximum_error,
        "limits": (
            "Source and engineering evidence only. Timing uses clock intervals, "
            "not direct one-way latency. Visible depth omits RPI. "
            "No fills, account PnL or forward-return labels measured."
        ),
        "forward_return_labels_computed": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "result.json", result)
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print(
        {
            key: result[key]
            for key in [
                "source_ready_for_longer_forward_capture",
                "failure_reasons",
                "capture_rows",
                "feature_rows",
                "maximum_feature_error",
            ]
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--quality-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.capture.absolute(), args.quality_plan.absolute(), args.output.absolute())
