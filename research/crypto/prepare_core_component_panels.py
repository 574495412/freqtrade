"""Export outcome-free, already frozen core predictions for a new risk/capacity diagnostic."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def prepare(learning, panel, output):
    if output.exists():
        raise ValueError("Preserve prior forecast export")
    receipt = read(learning / "receipt.json")
    for name, key in [
        ("all_predictions.parquet", "predictions_sha256"),
        ("selected_predictions.parquet", "selected_predictions_sha256"),
    ]:
        if sha(learning / name) != receipt[key]:
            raise ValueError("Frozen model forecasts changed")
    source = read(panel / "receipt.json")
    if source["file_hashes"]["all_features.parquet"] != sha(panel / "all_features.parquet"):
        raise ValueError("Frozen ATR/features changed")
    features = pd.read_parquet(panel / "all_features.parquet")
    predictions = pd.read_parquet(learning / "selected_predictions.parquet")
    core = predictions.loc[predictions.sleeve.eq("core")].merge(
        features[["row_id", "atr"]], on="row_id", validate="many_to_one"
    )
    output.mkdir(parents=True)
    policies = {}
    for policy in ["LinearStatic", "BoostStatic", "BoostMonthly"]:
        folder, hashes = output / policy, {}
        folder.mkdir()
        for symbol in sorted(features.symbol.unique()):
            rows = core.loc[
                core.model.eq(policy) & core.symbol.eq(symbol),
                ["date", "side", "atr", "row_id", "probability", "predicted_net"],
            ]
            rows = rows.rename(columns={"side": "forecast_side", "atr": "feature_atr"}).sort_values(
                "date"
            )
            if rows.date.duplicated().any():
                raise ValueError("More than one frozen direction per coin/time")
            path = folder / f"{symbol}.parquet"
            rows.to_parquet(path, index=False)
            hashes[symbol] = sha(path)
        policies[policy] = {
            "path": str(folder.absolute()),
            "hashes": hashes,
            "core_opportunities": int(core.model.eq(policy).sum()),
        }
    result = {
        "exported_utc": datetime.now(UTC).isoformat(),
        "export_source_sha256": sha(Path(__file__)),
        "source_forecast_receipt_sha256": sha(learning / "receipt.json"),
        "source_feature_receipt_sha256": sha(panel / "receipt.json"),
        "policies": policies,
        "future_prices_or_outcomes_exported": False,
        "original_unit_admission_changed": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "receipt.json", result)
    print(
        {
            "exported_core_opportunities": {
                k: v["core_opportunities"] for k, v in policies.items()
            },
            "no_future_outcomes_exported": True,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learning", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.learning, args.panel, args.output)
