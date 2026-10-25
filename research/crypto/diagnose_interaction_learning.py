"""Postscreen descriptive attribution only; no refits, new gates or alpha declarations."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.interaction_features import FEATURES
from research.crypto.interaction_models import enable_runtime


def diagnose(plan_path, panel, learning, evaluation):
    plan, receipt = read(plan_path), read(learning / "receipt.json")
    enable_runtime(plan)
    import joblib

    before = {
        name: sha(learning / name)
        for name in ["all_predictions.parquet", "selected_predictions.parquet"]
    }
    features = pd.read_parquet(panel / "validation_features.parquet")
    labels = pd.read_parquet(evaluation / "validation_units.parquet")
    keys = ["symbol", "date", "sleeve", "side"]
    lookup = labels.set_index(keys).net_label_3bps
    selected = pd.read_parquet(learning / "selected_predictions.parquet")
    selected = selected.merge(
        labels[["row_id", "net_label_3bps", "price_return", "funding_credit", "fee_multiplier"]],
        on="row_id",
        validate="many_to_one",
    )
    selected = selected.merge(
        features[["row_id", "signed_btc288"]], on="row_id", validate="many_to_one"
    )
    selected["month"] = selected.date.dt.strftime("%Y-%m")
    monthly = []
    for (model, sleeve, month), group in selected.groupby(["model", "sleeve", "month"]):
        monthly.append(
            {
                "model": model,
                "sleeve": sleeve,
                "month": month,
                "units": len(group),
                "positive_unit_fraction": float(group.net_label_3bps.gt(0).mean()),
                "mean_net_label": float(group.net_label_3bps.mean()),
                "long_units": int(group.side.eq(1).sum()),
                "short_units": int(group.side.eq(-1).sum()),
            }
        )
    baselines = []
    for model, group in selected.loc[selected.sleeve.eq("core")].groupby("model"):
        sides = np.sign(group.signed_btc288.to_numpy() / group.side.to_numpy()).astype(int)
        values = []
        for row, side in zip(group.itertuples(index=False), sides, strict=True):
            values.append(
                0.0 if side == 0 else float(lookup.loc[(row.symbol, row.date, "core", int(side))])
            )
        values = np.asarray(values)
        baselines.append(
            {
                "model": model,
                "selected_units": len(group),
                "same_side_as_past_BTC24h_trend_fraction": float(
                    np.mean(sides == group.side.to_numpy())
                ),
                "model_mean_unit_label": float(group.net_label_3bps.mean()),
                "same_selected_times_BTCtrend_direction_mean_label": float(values.mean()),
                "descriptive_mean_increment": float(
                    np.mean(group.net_label_3bps.to_numpy() - values)
                ),
            }
        )
    linear = selected.loc[selected.model.eq("LinearStatic") & selected.sleeve.eq("small")]
    linear_meta = next(m for m in receipt["model_metadata"] if m["key"] == "linear_small_202407")
    path = learning / "models/linear_small_202407.joblib"
    if sha(path) != linear_meta["model_sha256"]:
        raise ValueError("Original model changed")
    bundle = joblib.load(path)["linear"]
    x = features.set_index("row_id").loc[linear.row_id, FEATURES].to_numpy()
    scaled = (x - np.asarray(bundle["mean"])) / np.asarray(bundle["deviation"])
    maxima = np.abs(scaled).max(axis=1) if len(x) else np.array([])
    predictions = pd.read_parquet(learning / "all_predictions.parquet")
    ranges = []
    for (model, sleeve), group in predictions.groupby(["model", "sleeve"]):
        ranges.append(
            {
                "model": model,
                "sleeve": sleeve,
                "maximum_calibrated_probability": float(group.probability.max()),
                "maximum_raw_probability": float(group.raw_probability.max()),
            }
        )
    for name, digest in before.items():
        if sha(learning / name) != digest:
            raise ValueError("Frozen forecasts changed")
    return {
        "plan_sha256": sha(plan_path),
        "source_sha256": sha(Path(__file__)),
        "monthly_unit_labels": monthly,
        "matched_time_direction_diagnostics": baselines,
        "probability_ranges": ranges,
        "linear_small_training_standardized_feature_diagnostic": {
            "selected_units": len(linear),
            "max_absolute_training_std": float(maxima.max()) if len(maxima) else None,
            "units_with_any_feature_above_10_training_std": int((maxima > 10).sum()),
            "scope": "Posthoc descriptive flag; no units removed and no threshold added.",
        },
        "scope": "Descriptive diagnostics on already evaluated history. BTCtrend only changes "
        "direction at model-selected coin/times; it is not a separate strategy benchmark or "
        "an independently tested alpha comparison. Monthly means are not account returns.",
        "frozen_admission_changed": False,
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--learning", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    args = parser.parse_args()
    destination = args.evaluation / "postscreen_diagnostics.json"
    if destination.exists():
        raise ValueError("Preserve previous diagnostics")
    result = diagnose(args.plan, args.panel, args.learning, args.evaluation)
    save(destination, result)
    print(
        {
            "matched_direction": result["matched_time_direction_diagnostics"],
            "linear_small": result["linear_small_training_standardized_feature_diagnostic"],
            "probability_ranges": result["probability_ranges"],
        }
    )
