"""Prepare chronological interaction features and H1 labels without H2 outcome evaluation."""

import argparse
import gc
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.interaction_features import (
    FEATURES,
    funding_features,
    hourly_features,
    label_rows,
    opportunity_rows,
    read_market,
)
from research.crypto.interaction_models import verify_sources


def prepare(plan_path, output):
    plan = read(plan_path)
    verify_sources(plan)
    if output.exists():
        raise ValueError("Preserve previous prepared population")
    dataset = Path(plan["dataset"])
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Dataset receipt changed")
    receipts = read(dataset / "receipts.json")
    start, cut, end = [pd.Timestamp(plan[k], tz="UTC") for k in ["start", "evaluate_start", "end"]]
    output.mkdir(parents=True)
    source_folder = output / "sources"
    source_folder.mkdir()
    for source in plan["source_hashes"]:
        shutil.copyfile(source, source_folder / Path(source).name)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "source_identity": source_identity(),
            "source_hashes": plan["source_hashes"],
            "before_features_and_labels": True,
        },
    )
    hourly, events, checks = {}, {}, []
    for symbol in plan["symbols"]:
        price = read_market(dataset, receipts, symbol, "5m-futures")
        price = price.loc[
            price.date.ge(pd.Timestamp(plan["warmup_start"], tz="UTC")) & price.date.lt(end)
        ].set_index("date")
        if not price.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
            raise ValueError("Noncontinuous base price clock")
        hourly[symbol] = hourly_features(price)
        events[symbol] = read_market(dataset, receipts, symbol, "1h-funding_rate")
        if symbol in plan["prefix_symbols"]:
            for raw_cut in plan["prefix_cuts"]:
                clock = pd.Timestamp(raw_cut, tz="UTC")
                expected = hourly[symbol].loc[hourly[symbol].index <= clock]
                actual = hourly_features(price.loc[price.index <= clock])
                pd.testing.assert_frame_equal(expected, actual, check_exact=True)
                available = clock + pd.Timedelta(minutes=5)
                full = funding_features(events[symbol])
                prefix = funding_features(
                    events[symbol].loc[events[symbol].date + pd.Timedelta(hours=1) <= available]
                )
                pd.testing.assert_frame_equal(
                    full.loc[full.available_at <= available].reset_index(drop=True),
                    prefix.reset_index(drop=True),
                    check_exact=True,
                )
                checks.append(
                    {
                        "symbol": symbol,
                        "source_cut": clock.isoformat(),
                        "hourly_feature_rows": len(actual),
                        "price_columns": len(actual.columns),
                        "funding_rows": len(prefix),
                        "passed": True,
                    }
                )
        print({"prepared_features": symbol, "hourly_rows": len(hourly[symbol])}, flush=True)
        del price
        gc.collect()
    returns = pd.concat({symbol: frame.r12 for symbol, frame in hourly.items()}, axis=1)
    median = returns.median(axis=1).where(returns.notna().sum(axis=1).eq(len(plan["symbols"])))
    records = []
    for symbol in plan["symbols"]:
        if symbol == "BTCUSDT":
            continue
        frame = opportunity_rows(
            hourly[symbol], hourly["BTCUSDT"], median, events[symbol], start, end
        )
        frame.insert(0, "symbol", symbol)
        records.append(frame)
    rows = pd.concat(records, ignore_index=True)
    rows.insert(0, "row_id", range(len(rows)))
    if rows.duplicated(["symbol", "date", "sleeve", "side"]).any():
        raise ValueError("Duplicated decision unit")
    rows.to_parquet(output / "all_features.parquet", index=False)
    training = rows.loc[rows.date + pd.Timedelta(days=1, minutes=5) < cut]
    labels = label_rows(training, dataset, receipts, cut)
    training = training.merge(labels, on="row_id", validate="one_to_one")
    if not training.label_known_at.lt(cut).all():
        raise ValueError("H1 preparation used H2 outcomes")
    training.to_parquet(output / "h1_units.parquet", index=False)
    held = rows.loc[rows.date >= cut]
    held.to_parquet(output / "validation_features.parquet", index=False)
    save(
        output / "receipt.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "source_hashes": plan["source_hashes"],
            "all_feature_rows": len(rows),
            "h1_labeled_rows": len(training),
            "H2_feature_rows": len(held),
            "feature_count": len(FEATURES),
            "features": FEATURES,
            "actual_prefix_cases_passed": len(checks),
            "prefix_cases": checks,
            "validation_outcome_labels_computed": False,
            "file_hashes": {
                name: sha(output / name)
                for name in [
                    "all_features.parquet",
                    "h1_units.parquet",
                    "validation_features.parquet",
                ]
            },
            "native_source_identity": source_identity(),
            "economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "feature_rows": len(rows),
            "h1_labeled_rows": len(training),
            "H2_features": len(held),
            "features": len(FEATURES),
            "prefix_cases": len(checks),
            "H2_outcomes_computed": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan, args.output)
