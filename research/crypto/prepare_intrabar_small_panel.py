"""Reuse frozen causal features; compute new H1 complete-path labels after registration."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.first_hit_paths import causal_ticks, market_bundle
from research.crypto.interaction_features import FEATURES
from research.crypto.interaction_models import verify_sources
from research.crypto.intrabar_small_paths import SPECS, label_rows


def prepare(plan_path, output):
    plan = read(plan_path)
    verify_sources(plan)
    source = Path(plan["source_feature_panel"]["path"])
    if sha(source / "receipt.json") != plan["source_feature_panel"]["receipt_sha256"]:
        raise ValueError("Original feature receipt changed")
    receipt = read(source / "receipt.json")
    names = ["all_features.parquet", "validation_features.parquet"]
    for name in names:
        if sha(source / name) != receipt["file_hashes"][name]:
            raise ValueError("Frozen causal feature values changed")
    if output.exists() or plan["first_hit_specs"] != SPECS:
        raise ValueError("Existing output or incorrect fixed lifecycle")
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "source_identity": source_identity(),
            "before_new_path_outcomes": True,
        },
    )
    features = pd.read_parquet(source / names[0])
    held = pd.read_parquet(source / names[1])
    for frame in [features, held]:
        frame["horizon_hours"] = frame.sleeve.map({k: v["hours"] for k, v in SPECS.items()})
    cutoff = pd.Timestamp(plan["evaluate_start"], tz="UTC")
    first_half = features.loc[(features.date + pd.Timedelta(days=1, minutes=5)) < cutoff]
    raw_receipts = read(Path(plan["dataset"]) / "receipts.json")
    labels = label_rows(first_half, plan["dataset"], raw_receipts, cutoff)
    h1 = first_half.merge(labels, on="row_id", validate="one_to_one")
    features.to_parquet(output / names[0], index=False)
    held.to_parquet(output / names[1], index=False)
    h1.to_parquet(output / "h1_units.parquet", index=False)
    prefixes = []
    for symbol in plan["prefix_symbols"]:
        prices, _, _ = market_bundle(plan["dataset"], plan["data_receipts_sha256"], symbol)
        for cut in plan["prefix_cuts"]:
            clock = pd.Timestamp(cut, tz="UTC")
            prefix = prices.loc[prices.index < clock]
            actual = causal_ticks(prefix)
            expected = prices.known_tick.loc[prefix.index]
            pd.testing.assert_series_equal(actual, expected, check_exact=True)
            prefixes.append(
                {"symbol": symbol, "cut": clock.isoformat(), "rows": len(prefix), "passed": True}
            )
    summaries = []
    for sleeve, group in h1.loc[h1.risk_eligible].groupby("sleeve"):
        summaries.append(
            {
                "sleeve": sleeve,
                "risk_eligible_units": len(group),
                "mean_net_unit_label": float(group.net_label_3bps.mean()),
                "positive_unit_fraction": float(group.net_label_3bps.gt(0).mean()),
                "exit_counts": group.exit_reason.value_counts().to_dict(),
            }
        )
    result = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(plan_path),
        "source_hashes": plan["source_hashes"],
        "all_feature_rows": len(features),
        "h1_labeled_rows": len(h1),
        "H2_feature_rows": len(held),
        "feature_count": len(FEATURES),
        "features": FEATURES,
        "actual_precision_prefix_cases": prefixes,
        "original_feature_prefix_receipt_sha256": sha(source / "receipt.json"),
        "first_hit_H1_descriptive_units": summaries,
        "H2_outcomes_computed": False,
        "file_hashes": {name: sha(output / name) for name in [*names, "h1_units.parquet"]},
        "native_source_identity": source_identity(),
        "economic_accounts_computed": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "receipt.json", result)
    print(
        {
            "h1_paths": len(h1),
            "precision_prefixes": len(prefixes),
            "H1_descriptive_units": summaries,
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan, args.output)
