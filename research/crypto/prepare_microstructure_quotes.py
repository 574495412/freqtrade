"""Compact a closed registered source into clock-qualified observed quotes."""

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.microstructure_features import visible_book
from research.crypto.microstructure_quotes import clocks_for_receipts


def verify_receipts(path):
    for name, expected in read(path / "receipts.json").items():
        if sha(path / name) != expected:
            raise ValueError(f"Source changed: {name}")


def prepare(capture, analysis, plan_path, output):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve normalized quote source")
    verify_receipts(capture)
    verify_receipts(analysis)
    if read(analysis / "input_freeze.json")["capture_receipts_sha256"] != sha(
        capture / "receipts.json"
    ):
        raise ValueError("Feature analysis belongs to a different capture")
    plan, status = read(plan_path), read(capture / "status.json")
    if status["state"] != "terminal":
        raise ValueError("Capture must be authoritatively terminal")
    output.mkdir(parents=True)
    (output / "sources").mkdir()
    for name in [
        "prepare_microstructure_quotes.py",
        "microstructure_quotes.py",
        "microstructure_features.py",
    ]:
        source = Path(__file__).with_name(name)
        (output / "sources" / name).write_bytes(source.read_bytes())
    save(output / "economic_plan.json", plan)
    save(
        output / "input_freeze.json",
        {
            "economic_plan_sha256": sha(plan_path),
            "capture_receipts_sha256": sha(capture / "receipts.json"),
            "analysis_receipts_sha256": sha(analysis / "receipts.json"),
            "sources": {p.name: sha(p) for p in (output / "sources").iterdir()},
            "forward_return_labels_computed": False,
        },
    )
    clocks = read(capture / "clock_samples.json")
    quotes, trades = [], []
    sequence = 0
    for chunk in read(capture / "chunks.json"):
        with gzip.open(capture / chunk["file"], "rt") as stream:
            for line in stream:
                record = json.loads(line)
                if record["sequence"] != sequence:
                    raise ValueError("Raw source sequence mismatch")
                sequence += 1
                message = json.loads(record["raw"])["data"]
                symbol = message["s"]
                if symbol not in plan["symbols"]:
                    raise ValueError("Unregistered symbol")
                common = {
                    "mono_ns": record["received_monotonic_ns"],
                    "wall_ns": record["received_wall_ns"],
                    "epoch": record["epoch"],
                    "event_ms": int(message["E"]),
                    "symbol": symbol,
                }
                if record["channel"] == "depth":
                    try:
                        parsed = visible_book(message)
                        item = dict(
                            common,
                            bid=parsed["bid"],
                            ask=parsed["ask"],
                            valid_schema=True,
                            bid_text=message["b"][0][0],
                            ask_text=message["a"][0][0],
                        )
                    except (ValueError, KeyError, TypeError):
                        item = dict(
                            common,
                            bid=np.nan,
                            ask=np.nan,
                            valid_schema=False,
                            bid_text=None,
                            ask_text=None,
                        )
                    item["update_id"] = int(message["u"])
                    item["previous_update_id"] = int(message["pu"])
                    quotes.append(item)
                elif record["channel"] == "trades":
                    trades.append(common)
                else:
                    raise ValueError("Unexpected source channel")
    if sequence != status["raw_rows"]:
        raise ValueError("Capture row count mismatch")
    for values, kind in [(quotes, "quotes"), (trades, "trade_clock")]:
        frame = pd.DataFrame(values)
        timing = clocks_for_receipts(frame, clocks, plan["common_filters"])
        frame = pd.concat([frame, timing], axis=1)
        frame["valid"] = frame.clock_valid & frame.get("valid_schema", True)
        for symbol in plan["symbols"]:
            subset = frame.loc[frame.symbol.eq(symbol)].reset_index(drop=True)
            if kind == "quotes":
                same_epoch = subset.epoch.eq(subset.epoch.shift(1))
                subset["source_gap"] = same_epoch & subset.previous_update_id.ne(
                    subset.update_id.shift(1)
                )
                subset["valid"] &= ~(same_epoch & subset.update_id.le(subset.update_id.shift(1)))
            subset.to_parquet(output / f"{symbol}_{kind}.parquet", index=False)
    target = output / "features.parquet"
    target.write_bytes((analysis / "features.parquet").read_bytes())
    events = [json.loads(line) for line in (capture / "events.jsonl").read_text().splitlines()]
    ended = next(e for e in reversed(events) if e["event"] == "capture_ended")
    result = {
        "source_capture": capture.name,
        "raw_records": sequence,
        "quote_records": len(quotes),
        "trade_records": len(trades),
        "capture_end_monotonic_ns": ended["monotonic_ns"],
        "source_engineering_passed": read(analysis / "result.json")[
            "source_ready_for_longer_forward_capture"
        ],
        "forward_return_labels_computed": False,
        "production_eligible": False,
    }
    save(output / "result.json", result)
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(
        args.capture.absolute(),
        args.analysis.absolute(),
        args.plan.absolute(),
        args.output.absolute(),
    )
