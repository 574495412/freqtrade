"""Verify frozen raw activity and enroll causal events without reading any outcomes."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.activity_absorption_features import (
    GROUPS,
    dense_price_features,
    opportunities,
    read_activity_archive,
)
from research.crypto.interaction_features import hourly_features, read_market
from research.crypto.interaction_models import verify_sources


def prepare(plan_path, output):  # noqa: C901 - ordered source admission and causal enrollment
    plan = read(plan_path)
    verify_sources(plan)
    if output.exists():
        raise ValueError("Preserve prior enrollment")
    dataset, archives = Path(plan["dataset"]), Path(plan["flow_archives"])
    receipts, raw_receipts = read(dataset / "receipts.json"), read(archives / "receipts.json")
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Native data receipt changed")
    if sha(archives / "receipts.json") != plan["flow_archive_receipts_sha256"]:
        raise ValueError("Raw activity receipt changed")
    if sha(archives / "archive_sources.json") != raw_receipts["archive_sources.json"]:
        raise ValueError("Archive source list changed")
    sources = read(archives / "archive_sources.json")
    start, end = [pd.Timestamp(plan[k], tz="UTC") for k in ["feature_start", "end"]]
    calendar = pd.date_range(start, end, freq="5min", inclusive="left")
    prices = {}
    for symbol in plan["symbols"]:
        native = read_market(dataset, receipts, symbol, "5m-futures").set_index("date")
        native = native.loc[(native.index >= start) & (native.index < end)]
        if not native.index.equals(calendar):
            raise ValueError("Native calendar incomplete")
        prices[symbol] = native
    closes = pd.DataFrame({s: p.close for s, p in prices.items()})
    median = np.log(closes / closes.shift(12)).median(axis=1)
    reference = dense_price_features(prices["BTCUSDT"])
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "native_source_identity": source_identity(),
            "before_activity_enrollment": True,
            "outcomes_read": False,
        },
    )
    frames, consumed, prefixes, coverage = [], [], [], []
    for symbol, native in prices.items():
        selected = [
            r
            for r in sources
            if r["symbol"] == symbol
            and r["timeframe"] == "5m"
            and plan["feature_start"][:7] <= r["period"][:7] < plan["end"][:7]
        ]
        parts = []
        for record in selected:
            path = Path(record["path"])
            if (
                sha(path) != record["sha256"]
                or raw_receipts[str(path.relative_to(archives))] != record["sha256"]
            ):
                raise ValueError("Frozen raw activity archive changed")
            parts.append(read_activity_archive(path))
            consumed.append(record)
        source = pd.concat(parts).sort_values("date").set_index("date")
        if not source.index.equals(calendar):
            raise ValueError("Raw source calendar incomplete or duplicated")
        for name in ["open", "high", "low", "close", "volume"]:
            if not np.allclose(native[name], source[name], rtol=1e-12, atol=1e-12):
                raise ValueError("Raw activity OHLCV differs from native price")
        pd.testing.assert_frame_equal(
            dense_price_features(native).loc[lambda f: f.index.minute == 0], hourly_features(native)
        )
        events = read_market(dataset, receipts, symbol, "1h-funding_rate")
        joined = native.join(source[["quote_volume", "trade_count", "taker_buy_quote"]])
        units = opportunities(
            joined, reference, median, events, pd.Timestamp(plan["start"], tz="UTC"), end
        )
        if symbol != "BTCUSDT":
            frames.append(units.assign(symbol=symbol))
        if symbol in plan["prefix_symbols"]:
            for value in plan["prefix_cuts"]:
                cut = pd.Timestamp(value, tz="UTC")
                observed = opportunities(
                    joined.loc[joined.index < cut],
                    reference.loc[reference.index < cut],
                    median.loc[median.index < cut],
                    events.loc[events.date < cut],
                    pd.Timestamp(plan["start"], tz="UTC"),
                    end,
                )
                expected = units.loc[units.feature_open < cut]
                pd.testing.assert_frame_equal(
                    observed.sort_values(["date", "side"]).reset_index(drop=True),
                    expected.sort_values(["date", "side"]).reset_index(drop=True),
                )
                prefixes.append({"symbol": symbol, "cut": value, "events_checked": len(expected)})
        coverage.append(
            {
                "symbol": symbol,
                "raw_rows": len(source),
                "events": len(units),
                "archives": len(selected),
                "hourly25feature_parity": True,
            }
        )
        print(coverage[-1], flush=True)
    all_features = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date", "side"])
    all_features = all_features.reset_index(drop=True)
    all_features["row_id"] = np.arange(len(all_features))
    path = output / "all_features.parquet"
    all_features.to_parquet(path, index=False)
    monthly = all_features.groupby(all_features.date.dt.strftime("%Y-%m")).size().to_dict()
    save(output / "sources.json", consumed)
    result = {
        "completed_utc": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(plan_path),
        "preparer_sha256": sha(Path(__file__)),
        "feature_hash": sha(path),
        "source_archive_count": len(consumed),
        "source_rows": sum(r["raw_rows"] for r in coverage),
        "coverage": coverage,
        "events": len(all_features),
        "event_counts_by_month": monthly,
        "non_hourly_events": int(all_features.feature_open.dt.minute.ne(0).sum()),
        "features_by_group": GROUPS,
        "actual_prefix_checks": prefixes,
        "native_source_files_unchanged": len(source_identity()),
        "outcomes_read": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "receipt.json", result)
    print(
        {"enrolled_events": len(all_features), "monthly_counts": monthly, "outcomes_read": False},
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan, args.output)
