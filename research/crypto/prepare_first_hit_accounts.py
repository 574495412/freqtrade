"""Export frozen first-hit signals and known ATR, without evaluation outcomes."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha


def prepare(study, output):
    if output.exists():
        raise ValueError("Preserve prior signal export")
    learning, panel = study / "learning", study / "panel"
    receipt, prepared = read(learning / "receipt.json"), read(panel / "receipt.json")
    for path, digest in [
        (learning / "selected_predictions.parquet", receipt["selected_predictions_sha256"]),
        (panel / "all_features.parquet", prepared["file_hashes"]["all_features.parquet"]),
    ]:
        if sha(path) != digest:
            raise ValueError("Frozen signal or feature changed")
    features = pd.read_parquet(panel / "all_features.parquet")
    forecasts = pd.read_parquet(learning / "selected_predictions.parquet").merge(
        features[["row_id", "atr"]], on="row_id", validate="many_to_one"
    )
    output.mkdir(parents=True)
    policies = {}
    for policy in ["LinearStatic", "BoostStatic", "BoostMonthly"]:
        folder, hashes = output / policy, {}
        folder.mkdir()
        for symbol in sorted(features.symbol.unique()):
            rows = forecasts.loc[
                forecasts.model.eq(policy) & forecasts.symbol.eq(symbol),
                ["date", "side", "sleeve", "atr", "row_id", "probability", "predicted_net"],
            ].rename(
                columns={"side": "forecast_side", "sleeve": "forecast_sleeve", "atr": "feature_atr"}
            )
            if rows.date.duplicated().any():
                raise ValueError("More than one frozen entry per coin and clock")
            path = folder / f"{symbol}.parquet"
            rows.sort_values("date").to_parquet(path, index=False)
            hashes[symbol] = sha(path)
        selected = forecasts.loc[forecasts.model.eq(policy)]
        policies[policy] = {
            "path": str(folder.absolute()),
            "hashes": hashes,
            "signals_per_sleeve": selected.sleeve.value_counts().to_dict(),
        }
    result = {
        "exported_utc": datetime.now(UTC).isoformat(),
        "policies": policies,
        "learning_receipt_sha256": sha(learning / "receipt.json"),
        "feature_receipt_sha256": sha(panel / "receipt.json"),
        "export_source_sha256": sha(Path(__file__)),
        "future_prices_or_outcomes_exported": False,
        "previous_unit_admission_changed": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "receipt.json", result)
    print({"exported": {k: v["signals_per_sleeve"] for k, v in policies.items()}}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.study, args.output)
