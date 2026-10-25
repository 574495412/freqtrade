"""Freeze chronological forecasts trained on complete first-hit paths."""

import argparse
import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.interaction_features import FEATURES
from research.crypto.interaction_models import (
    enable_runtime,
    fit_bundle,
    predict_bundle,
    scalar_predict,
    verify_sources,
)
from research.crypto.intrabar_small_paths import label_rows


def window_rows(rows, start, end):
    return rows.loc[
        rows.date.ge(start) & (rows.date + pd.Timedelta(days=1, minutes=5)).lt(end)
    ].copy()


def select_predictions(predictions, plan):
    frame = predictions.copy()
    probability = frame.sleeve.map(plan["minimum_probability"])
    minimum_net = frame.sleeve.map(plan["minimum_predicted_net"])
    frame = frame.loc[
        frame.probability.ge(probability) & frame.predicted_net.ge(minimum_net)
    ].copy()
    frame = frame.sort_values(
        ["model", "symbol", "date", "sleeve", "predicted_net", "side"],
        ascending=[True, True, True, True, False, False],
    )
    frame = frame.drop_duplicates(["model", "symbol", "date", "sleeve"])
    frame["priority"] = frame.sleeve.eq("core").astype(int)
    return (
        frame.sort_values(
            ["model", "symbol", "date", "priority"], ascending=[True, True, True, False]
        )
        .drop_duplicates(["model", "symbol", "date"])
        .drop(columns="priority")
    )


def learn(plan_path, panel, output):  # noqa: C901 - ordered prequential fits and immutable forecasts
    plan = read(plan_path)
    verify_sources(plan)
    versions = enable_runtime(plan)
    import joblib
    from threadpoolctl import threadpool_limits

    receipt = read(panel / "receipt.json")
    if receipt["plan_sha256"] != sha(plan_path) or output.exists():
        raise ValueError("Mismatched panel or existing learning output")
    for name, digest in receipt["file_hashes"].items():
        if sha(panel / name) != digest:
            raise ValueError("Prepared feature or H1 label changed")
    identity = source_identity()
    if read(panel / "freeze.json")["source_identity"] != identity:
        raise ValueError("Native framework changed")
    output.mkdir(parents=True)
    (output / "models").mkdir()
    (output / "forecasts").mkdir()
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "panel_receipt_sha256": sha(panel / "receipt.json"),
            "runtime_versions": versions,
            "native_source_identity": identity,
            "before_model_fits": True,
        },
    )
    features = pd.read_parquet(panel / "all_features.parquet")
    h1 = pd.read_parquet(panel / "h1_units.parquet")
    label_fields = [
        "row_id",
        "entry",
        "label_known_at",
        "entry_price",
        "exit_price",
        "price_return",
        "funding_credit",
        "fee_multiplier",
        "net_label_3bps",
        "risk_eligible",
    ]
    cache = h1[label_fields].set_index("row_id")
    held = pd.read_parquet(panel / "validation_features.parquet")
    receipts = read(Path(plan["dataset"]) / "receipts.json")
    fitted, metadata, predictions, admissions, errors = {}, [], [], [], []
    months = pd.date_range(
        pd.Timestamp(plan["evaluate_start"], tz="UTC"),
        pd.Timestamp(plan["end"], tz="UTC"),
        freq="MS",
        inclusive="left",
    )
    control = Path(plan["frozen_core_learning"]["path"])
    if sha(control / "receipt.json") != plan["frozen_core_learning"]["receipt_sha256"]:
        raise ValueError("Frozen core-control receipt changed")
    control_receipt = read(control / "receipt.json")
    if sha(control / "all_predictions.parquet") != control_receipt["predictions_sha256"]:
        raise ValueError("Frozen core forecasts changed")
    core_forecasts = pd.read_parquet(control / "all_predictions.parquet")
    core_forecasts = core_forecasts.loc[
        core_forecasts.sleeve.eq("core") & core_forecasts.model.eq("BoostMonthly")
    ].copy()
    core_forecasts = pd.concat(
        [core_forecasts.assign(model=name) for name in plan["models"]], ignore_index=True
    )
    for record in control_receipt["model_metadata"]:
        if record["sleeve"] != "core" or record["kind"] != "boosted":
            continue
        key = record["key"]
        for suffix, digest in [
            (".joblib", record["model_sha256"]),
            ("_population.parquet", record["population_sha256"]),
        ]:
            path = control / "models" / f"{key}{suffix}"
            if sha(path) != digest:
                raise ValueError("Frozen core model/population changed")
            shutil.copyfile(path, output / "models" / path.name)
        metadata.append(dict(record, reused_core_control=True))
    with threadpool_limits(limits=plan["maximum_model_threads"]):
        for number, month in enumerate(months):
            next_month = month + pd.offsets.MonthBegin(1)
            training_end = month - pd.Timedelta(days=18)
            training_start = training_end - pd.Timedelta(days=120)
            calibration_start, calibration_end = (
                month - pd.Timedelta(days=16),
                month - pd.Timedelta(days=2),
            )
            train = window_rows(features, training_start, training_end)
            calibration = window_rows(features, calibration_start, calibration_end)
            needed = pd.concat([train, calibration], ignore_index=True)
            missing = needed.loc[~needed.row_id.isin(cache.index)]
            if len(missing):
                new_labels = label_rows(missing, plan["dataset"], receipts, calibration_end)
                if not new_labels.label_known_at.lt(calibration_end).all():
                    raise ValueError("Rolling fit admitted future labels")
                new_labels.to_parquet(
                    output / f"admitted_past_units_{month:%Y%m}.parquet", index=False
                )
                cache = pd.concat([cache, new_labels.set_index("row_id")])
                if cache.index.has_duplicates:
                    raise ValueError("Label admitted twice")
                admissions.append(
                    {
                        "forecast_month": month.isoformat(),
                        "new_past_units": len(new_labels),
                        "latest_outcome_utc": new_labels.label_known_at.max().isoformat(),
                        "cutoff_utc": calibration_end.isoformat(),
                    }
                )
            train = train.merge(cache.reset_index(), on="row_id", validate="one_to_one")
            calibration = calibration.merge(cache.reset_index(), on="row_id", validate="one_to_one")
            if (
                not train.label_known_at.lt(training_end).all()
                or not calibration.label_known_at.lt(calibration_end).all()
            ):
                raise ValueError("Model window crossed its chronological cutoff")
            train = train.loc[train.risk_eligible].copy()
            calibration = calibration.loc[calibration.risk_eligible].copy()
            current = held.loc[held.date.ge(month) & held.date.lt(next_month)]
            monthly = []
            for sleeve in ["small"]:
                fit = train.loc[train.sleeve.eq(sleeve)]
                cal = calibration.loc[calibration.sleeve.eq(sleeve)]
                predict_rows = current.loc[current.sleeve.eq(sleeve)]
                if (
                    len(fit) < plan["minimum_fit_rows"]
                    or len(cal) < plan["minimum_calibration_rows"]
                ):
                    raise ValueError("Registered training population insufficient")
                kinds = ["linear", "boosted"] if number == 0 else ["boosted"]
                for kind in kinds:
                    key = f"{kind}_{sleeve}_{month:%Y%m}"
                    bundle = fit_bundle(
                        kind,
                        fit[FEATURES].to_numpy(),
                        fit.net_label_3bps.to_numpy(),
                        cal[FEATURES].to_numpy(),
                        cal.net_label_3bps.to_numpy(),
                        plan,
                    )
                    population = pd.concat(
                        [
                            fit[["row_id", "label_known_at", "net_label_3bps"]].assign(
                                role="train"
                            ),
                            cal[["row_id", "label_known_at", "net_label_3bps"]].assign(
                                role="calibration"
                            ),
                        ],
                        ignore_index=True,
                    )
                    population.to_parquet(
                        output / "models" / f"{key}_population.parquet", index=False
                    )
                    path = output / "models" / f"{key}.joblib"
                    joblib.dump(bundle, path)
                    fitted[key] = bundle
                    raw, probability, net = predict_bundle(
                        bundle, cal[FEATURES].iloc[:5].to_numpy()
                    )
                    for i, row in enumerate(cal[FEATURES].iloc[:5].to_numpy()):
                        scalar = scalar_predict(bundle, row)
                        error = max(
                            abs(scalar[j] - observed[i])
                            for j, observed in enumerate([raw, probability, net])
                        )
                        if error > 1e-10:
                            raise ValueError(
                                "Scalar model traversal differs from library prediction"
                            )
                        errors.append(error)
                    record = {
                        "key": key,
                        "kind": kind,
                        "sleeve": sleeve,
                        "forecast_start": month.isoformat(),
                        "training_start": training_start.isoformat(),
                        "training_end": training_end.isoformat(),
                        "calibration_start": calibration_start.isoformat(),
                        "calibration_end": calibration_end.isoformat(),
                        "fit_rows": len(fit),
                        "calibration_rows": len(cal),
                        "training_positive_fraction": float(fit.net_label_3bps.gt(0).mean()),
                        "calibration_positive_fraction": float(cal.net_label_3bps.gt(0).mean()),
                        "training_net_mean": float(fit.net_label_3bps.mean()),
                        "latest_fit_outcome": fit.label_known_at.max().isoformat(),
                        "latest_calibration_outcome": cal.label_known_at.max().isoformat(),
                        "model_sha256": sha(path),
                        "population_sha256": sha(output / "models" / f"{key}_population.parquet"),
                        "calibration": bundle["calibration"],
                        "fit_row_ids_sha256": hashlib.sha256(
                            fit.row_id.to_numpy(dtype="int64").tobytes()
                        ).hexdigest(),
                    }
                    metadata.append(record)
                    save(
                        output / "model_checkpoint.json",
                        {
                            "completed_bundles": metadata,
                            "upcoming_forecast_month": month.isoformat(),
                            "all_future_forecast_labels_excluded": True,
                        },
                    )
                    print(
                        {"fitted": key, "fit_rows": len(fit), "calibration_rows": len(cal)},
                        flush=True,
                    )
                for name, key in [
                    ("LinearStatic", f"linear_{sleeve}_{months[0]:%Y%m}"),
                    ("BoostStatic", f"boosted_{sleeve}_{months[0]:%Y%m}"),
                    ("BoostMonthly", f"boosted_{sleeve}_{month:%Y%m}"),
                ]:
                    raw, probability, net = predict_bundle(
                        fitted[key], predict_rows[FEATURES].to_numpy()
                    )
                    forecast = predict_rows[
                        [
                            "row_id",
                            "symbol",
                            "date",
                            "feature_open",
                            "side",
                            "sleeve",
                            "horizon_hours",
                        ]
                    ].copy()
                    forecast["model"], forecast["model_key"] = name, key
                    (
                        forecast["raw_probability"],
                        forecast["probability"],
                        forecast["predicted_net"],
                    ) = raw, probability, net
                    monthly.append(forecast)
            monthly.append(
                core_forecasts.loc[
                    core_forecasts.date.ge(month) & core_forecasts.date.lt(next_month)
                ].copy()
            )
            month_frame = pd.concat(monthly, ignore_index=True)
            month_frame.to_parquet(output / "forecasts" / f"{month:%Y%m}.parquet", index=False)
            predictions.append(month_frame)
            print(
                {"frozen_forecast_month": month.isoformat(), "prediction_rows": len(month_frame)},
                flush=True,
            )
    forecast = pd.concat(predictions, ignore_index=True)
    if forecast.duplicated(["model", "row_id"]).any():
        raise ValueError("Duplicate forecast")
    pd.testing.assert_frame_equal(
        forecast.loc[forecast.sleeve.eq("core")]
        .sort_values(["model", "row_id"])
        .reset_index(drop=True),
        core_forecasts.sort_values(["model", "row_id"]).reset_index(drop=True),
        check_exact=True,
    )
    selected = select_predictions(forecast, plan)
    forecast.to_parquet(output / "all_predictions.parquet", index=False)
    selected.to_parquet(output / "selected_predictions.parquet", index=False)
    if source_identity() != identity:
        raise ValueError("Native engine changed")
    save(
        output / "receipt.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "panel_receipt_sha256": sha(panel / "receipt.json"),
            "model_metadata": metadata,
            "newly_fitted_small_bundles": 7,
            "reused_core_control_bundles": 6,
            "core_control_receipt_sha256": sha(control / "receipt.json"),
            "past_H2_label_admissions": admissions,
            "predictions_sha256": sha(output / "all_predictions.parquet"),
            "selected_predictions_sha256": sha(output / "selected_predictions.parquet"),
            "forecast_month_hashes": {
                p.name: sha(p) for p in (output / "forecasts").glob("*.parquet")
            },
            "selected_counts": [
                {
                    "model": model,
                    "sleeve": sleeve,
                    "units": int(((selected.model == model) & (selected.sleeve == sleeve)).sum()),
                }
                for model in plan["models"]
                for sleeve in ["small", "core"]
            ],
            "scalar_prediction_checks": len(errors),
            "maximum_scalar_prediction_error": max(errors),
            "all_forecasts_frozen_before_evaluation": True,
            "rolling_policy": "Past H2 labels admitted for later months only after own-month "
            "forecasts were frozen.",
            "native_source_files_unchanged": len(identity),
            "economic_accounts_computed": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "newly_fitted_small_bundles": 7,
            "reused_core_control_bundles": 6,
            "predictions": len(forecast),
            "selected": len(selected),
            "future_own_month_outcomes_used": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    learn(args.plan, args.panel, args.output)
