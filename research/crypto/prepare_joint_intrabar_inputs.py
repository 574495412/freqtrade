"""Outcome-free ranking inputs and reusable, causally computed precision proxies."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def prepare(study, output, precision_cache):
    if output.exists():
        raise ValueError("Preserve existing inputs")
    panel, learning = study / "panel", study / "learning"
    prep, learn = read(panel / "receipt.json"), read(learning / "receipt.json")
    for path, digest in [
        (panel / "all_features.parquet", prep["file_hashes"]["all_features.parquet"]),
        (learning / "selected_predictions.parquet", learn["selected_predictions_sha256"]),
    ]:
        if sha(path) != digest:
            raise ValueError("Frozen signals/features changed")
    features = pd.read_parquet(panel / "all_features.parquet")
    forecast = pd.read_parquet(learning / "selected_predictions.parquet").merge(
        features[["row_id", "atr", "atr_fraction"]], on="row_id", validate="many_to_one"
    )
    output.mkdir(parents=True)
    policies = {}
    for policy in ["LinearStatic", "BoostStatic", "BoostMonthly"]:
        folder = output / policy
        folder.mkdir()
        hashes = {}
        selected = forecast.loc[
            forecast.model.eq(policy),
            [
                "date",
                "symbol",
                "side",
                "sleeve",
                "atr",
                "atr_fraction",
                "row_id",
                "probability",
                "predicted_net",
            ],
        ].rename(
            columns={
                "side": "forecast_side",
                "sleeve": "forecast_sleeve",
                "atr": "feature_atr",
                "atr_fraction": "known_atr_fraction",
            }
        )
        if selected.duplicated(["date", "symbol"]).any():
            raise ValueError("Duplicate signal")
        rank_path = folder / "ranking.parquet"
        selected.to_parquet(rank_path, index=False)
        for symbol in sorted(features.symbol.unique()):
            path = folder / f"{symbol}.parquet"
            selected.loc[selected.symbol.eq(symbol)].drop(columns="symbol").to_parquet(
                path, index=False
            )
            hashes[symbol] = sha(path)
        policies[policy] = {
            "path": str(folder.absolute()),
            "hashes": hashes,
            "ranking_path": str(rank_path.absolute()),
            "ranking_sha256": sha(rank_path),
        }
    plan = read(Path("research/crypto/first_hit_learning_plan_v001.json"))
    manifest = read(precision_cache)
    if manifest["dataset_receipts_sha256"] != plan["data_receipts_sha256"] or manifest[
        "source_sha256"
    ] != sha(Path("research/crypto/first_hit_paths.py")):
        raise ValueError("Precision cache provenance changed")
    if len(manifest["prefix_cases"]) != 9 or not all(r["passed"] for r in manifest["prefix_cases"]):
        raise ValueError("Precision cache lacks actual causal prefixes")
    for source in manifest["files"].values():
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Cached precision series changed")
    receipt = {
        "prepared_utc": datetime.now(UTC).isoformat(),
        "policies": policies,
        "precision_cache": {
            "path": str(precision_cache.absolute()),
            "sha256": sha(precision_cache),
        },
        "learning_receipt_sha256": sha(learning / "receipt.json"),
        "feature_receipt_sha256": sha(panel / "receipt.json"),
        "source_sha256": sha(Path(__file__)),
        "future_outcomes_exported": False,
        "orders_enabled": False,
    }
    save(output / "receipt.json", receipt)
    print(
        {
            "policies": len(policies),
            "reused_precision_files": len(manifest["files"]),
            "precision_prefixes": len(manifest["prefix_cases"]),
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--precision-cache", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.study, args.output, args.precision_cache)
