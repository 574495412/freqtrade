"""Outcome-free ranking inputs and reusable, causally computed precision proxies."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.first_hit_paths import causal_ticks, market_bundle


def prepare(study, output):
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
    cache = output / "precision"
    cache.mkdir()
    files = {}
    prefixes = []
    for symbol in sorted(features.symbol.unique()):
        price, _, _ = market_bundle(plan["dataset"], plan["data_receipts_sha256"], symbol)
        path = cache / f"{symbol}.parquet"
        price[["known_tick"]].to_parquet(path)
        pd.testing.assert_series_equal(
            pd.read_parquet(path).known_tick, price.known_tick, check_exact=True
        )
        files[symbol] = {"path": str(path.absolute()), "sha256": sha(path)}
        if symbol in plan["prefix_symbols"]:
            for cut in plan["prefix_cuts"]:
                clock = pd.Timestamp(cut, tz="UTC")
                prefix = price.loc[price.index < clock]
                pd.testing.assert_series_equal(
                    causal_ticks(prefix), price.known_tick.loc[prefix.index], check_exact=True
                )
                prefixes.append({"symbol": symbol, "cut": clock.isoformat(), "passed": True})
    precision = {
        "files": files,
        "dataset_receipts_sha256": plan["data_receipts_sha256"],
        "start": "2023-12-01",
        "end": "2025-01-01",
        "prefix_cases": prefixes,
        "source_sha256": sha(Path("research/crypto/first_hit_paths.py")),
        "scope": (
            "Exact cached causal_ticks outputs, no new precision estimator or economic outcome."
        ),
    }
    save(cache / "receipt.json", precision)
    receipt = {
        "prepared_utc": datetime.now(UTC).isoformat(),
        "policies": policies,
        "precision_cache": {
            "path": str((cache / "receipt.json").absolute()),
            "sha256": sha(cache / "receipt.json"),
        },
        "learning_receipt_sha256": sha(learning / "receipt.json"),
        "feature_receipt_sha256": sha(panel / "receipt.json"),
        "source_sha256": sha(Path(__file__)),
        "future_outcomes_exported": False,
        "orders_enabled": False,
    }
    save(output / "receipt.json", receipt)
    print(
        {"policies": len(policies), "precision_files": len(files), "prefixes": len(prefixes)},
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.study, args.output)
