"""Immutable minute-detail overlay with explicit no-observed-trade masks; no price repair."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.enums import CandleType
from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.prepare_nfi import complete


def prepare(plan_path, output):
    plan = read(plan_path)
    if output.exists():
        raise ValueError("Preserve prior overlay")
    for item in plan["sources"]:
        if sha(Path(item["path"])) != item["sha256"]:
            raise ValueError("Source preparation implementation changed")
    base, source = Path(plan["base_dataset"]), Path(plan["minute_source"])
    if sha(base / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Original data receipt changed")
    quality = read(Path(plan["quality_audit"]["path"]))
    if sha(Path(plan["quality_audit"]["path"])) != plan["quality_audit"]["sha256"]:
        raise ValueError("Quality assessment changed")
    if any(
        x["positive_trade_ohlc_mismatches"] or x["volume_mismatches"] for x in quality["coverage"]
    ):
        raise ValueError("Unexplained positive-trade mismatch")
    records, receipts = read(source / "archive_sources.json"), read(base / "receipts.json")
    if len(records) != len(plan["symbols"]) * 6:
        raise ValueError("Actual raw source cardinality differs")
    output.mkdir(parents=True)
    save(output / "plan.json", plan)
    handler = FeatherDataHandler(output / "candles")
    (output / "masks").mkdir()
    hashes, masks, coverage = {}, {}, []
    for symbol in plan["symbols"]:
        stem = symbol.removesuffix("USDT") + "_USDT_USDT"
        for suffix in ["5m-futures", "1h-funding_rate", "1h-mark"]:
            name = f"candles/futures/{stem}-{suffix}.feather"
            original = base / name
            if sha(original) != receipts[name]:
                raise ValueError("Frozen base source changed")
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(original)
            hashes[name] = receipts[name]
        event_name = f"{symbol}_funding_events.parquet"
        if sha(base / event_name) != receipts[event_name]:
            raise ValueError("Funding event source changed")
        (output / event_name).symlink_to(base / event_name)
        hashes[event_name] = receipts[event_name]
        selected = sorted([r for r in records if r["symbol"] == symbol], key=lambda x: x["period"])
        for record in selected:
            if sha(Path(record["path"])) != record["sha256"]:
                raise ValueError("Raw minute archive changed")
        frame = complete(
            pd.concat([read_archive(Path(r["path"])) for r in selected]),
            plan["start"],
            plan["end"],
            "min",
        )
        pair = symbol.removesuffix("USDT") + "/USDT:USDT"
        handler.ohlcv_store(pair, "1m", frame, CandleType.FUTURES)
        name = f"candles/futures/{stem}-1m-futures.feather"
        hashes[name] = sha(output / name)
        mask = frame[["date"]].copy()
        mask["has_observed_trade"] = frame.volume.gt(0)
        mask_path = output / f"masks/{symbol}.parquet"
        mask.to_parquet(mask_path, index=False)
        hashes[str(mask_path.relative_to(output))] = sha(mask_path)
        masks[symbol] = {"path": str(mask_path.absolute()), "sha256": sha(mask_path)}
        coverage.append(
            {
                "symbol": symbol,
                "minutes": len(frame),
                "no_observed_trade_minutes": int((~mask.has_observed_trade).sum()),
            }
        )
        print(coverage[-1], flush=True)
    save(
        output / "detail_manifest.json",
        {
            "masks": masks,
            "minute_files": {k: v for k, v in hashes.items() if k.endswith("1m-futures.feather")},
            "start": plan["start"],
            "end": plan["end"],
            "raw_values_unchanged": True,
            "execution_gate_required": True,
            "parent_quality_sha256": plan["quality_audit"]["sha256"],
        },
    )
    hashes["detail_manifest.json"] = sha(output / "detail_manifest.json")
    save(output / "receipts.json", hashes)
    save(
        output / "preparation_receipt.json",
        {
            "prepared_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "coverage": coverage,
            "actual_archives": len(records),
            "raw_values_changed": False,
            "missing_candles_filled": False,
            "outcomes_computed": False,
            "requires_research_execution_gate": True,
            "production_eligible": False,
            "orders_enabled": False,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan, args.output)
