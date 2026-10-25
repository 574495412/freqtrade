"""Chronological nested-feature experiment on a frozen common absorption population."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.activity_absorption_features import GROUPS
from research.crypto.interaction_models import (
    enable_runtime,
    fit_bundle,
    predict_bundle,
    scalar_predict,
    verify_sources,
)
from research.crypto.intrabar_small_paths import label_rows
from research.crypto.run_intrabar_small_learning import select_predictions, window_rows
from research.crypto.screen_interaction_learning import bh_adjust, block_statistics


KEYS = ["row_id", "symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]


def prepare(plan_path, root):
    plan = read(plan_path)
    verify_sources(plan)
    source = Path(plan["enrollment"]["path"])
    receipt = read(source / "receipt.json")
    if (
        sha(source / "receipt.json") != plan["enrollment"]["receipt_sha256"]
        or sha(source / "all_features.parquet") != receipt["feature_hash"]
    ):
        raise ValueError("Enrolled common feature population changed")
    panel = root / "panel"
    panel.mkdir(parents=True, exist_ok=False)
    save(
        panel / "freeze.json",
        {
            "source_identity": source_identity(),
            "plan_sha256": sha(plan_path),
            "before_H1_labels": True,
        },
    )
    features = pd.read_parquet(source / "all_features.parquet")
    cut = pd.Timestamp(plan["evaluate_start"], tz="UTC")
    training = features.loc[(features.date + pd.Timedelta(days=1, minutes=5)) < cut]
    labels = label_rows(
        training, plan["dataset"], read(Path(plan["dataset"]) / "receipts.json"), cut
    )
    h1 = training.merge(labels, on="row_id", validate="one_to_one")
    for name, frame in [
        ("all_features", features),
        ("h1_units", h1),
        ("validation_features", features.loc[features.date >= cut]),
    ]:
        frame.to_parquet(panel / f"{name}.parquet", index=False)
    names = ["all_features.parquet", "h1_units.parquet", "validation_features.parquet"]
    save(
        panel / "receipt.json",
        {
            "plan_sha256": sha(plan_path),
            "file_hashes": {n: sha(panel / n) for n in names},
            "h1_units": len(h1),
            "H2_outcomes_computed": False,
        },
    )
    print({"H1_units": len(h1), "H2_features": int((features.date >= cut).sum())}, flush=True)


def learn(plan_path, root):
    plan = read(plan_path)
    verify_sources(plan)
    versions = enable_runtime(plan)
    import joblib
    from threadpoolctl import threadpool_limits

    panel, output = root / "panel", root / "learning"
    prepared = read(panel / "receipt.json")
    if prepared["plan_sha256"] != sha(plan_path):
        raise ValueError("Panel protocol mismatch")
    for name, digest in prepared["file_hashes"].items():
        if sha(panel / name) != digest:
            raise ValueError("Prepared input changed")
    (output / "models").mkdir(parents=True, exist_ok=False)
    (output / "forecasts").mkdir()
    save(
        output / "freeze.json",
        {"plan_sha256": sha(plan_path), "runtime": versions, "before_forecasts": True},
    )
    features = pd.read_parquet(panel / "all_features.parquet")
    held = pd.read_parquet(panel / "validation_features.parquet")
    h1 = pd.read_parquet(panel / "h1_units.parquet")
    fields = ["row_id", "risk_eligible", "net_label_3bps", "label_known_at"]
    cache = h1[fields].set_index("row_id")
    receipts = read(Path(plan["dataset"]) / "receipts.json")
    metadata, predictions = [], []
    months = pd.date_range(
        plan["evaluate_start"], plan["end"], tz="UTC", freq="MS", inclusive="left"
    )
    with threadpool_limits(limits=plan["maximum_model_threads"]):
        for month in months:
            train_end, cal_start, cal_end = [month - pd.Timedelta(days=n) for n in [18, 16, 2]]
            train_start = train_end - pd.Timedelta(days=120)
            train, cal = (
                window_rows(features, train_start, train_end),
                window_rows(features, cal_start, cal_end),
            )
            needed = pd.concat([train, cal])
            missing = needed.loc[~needed.row_id.isin(cache.index)]
            if len(missing):
                labels = label_rows(missing, plan["dataset"], receipts, cal_end)
                labels.to_parquet(output / f"admitted_past_units_{month:%Y%m}.parquet", index=False)
                cache = pd.concat([cache, labels[fields].set_index("row_id")])
            if cache.index.has_duplicates:
                raise ValueError("Outcome admitted twice")
            train = train.merge(cache.reset_index(), on="row_id", validate="one_to_one")
            cal = cal.merge(cache.reset_index(), on="row_id", validate="one_to_one")
            train, cal = train.loc[train.risk_eligible], cal.loc[cal.risk_eligible]
            if (
                not train.label_known_at.lt(train_end).all()
                or not cal.label_known_at.lt(cal_end).all()
                or len(train) < plan["minimum_fit_rows"]
                or len(cal) < plan["minimum_calibration_rows"]
            ):
                raise ValueError("Registered causal fit/calibration admission failed")
            current = held.loc[held.date.ge(month) & held.date.lt(month + pd.offsets.MonthBegin(1))]
            monthly = []
            for group, columns in GROUPS.items():
                key = f"{group}_{month:%Y%m}"
                bundle = fit_bundle(
                    "boosted",
                    train[columns].to_numpy(),
                    train.net_label_3bps.to_numpy(),
                    cal[columns].to_numpy(),
                    cal.net_label_3bps.to_numpy(),
                    plan,
                )
                path = output / "models" / f"{key}.joblib"
                joblib.dump(bundle, path)
                population = pd.concat(
                    [train[fields].assign(role="train"), cal[fields].assign(role="calibration")],
                    ignore_index=True,
                )
                pop_path = output / "models" / f"{key}_population.parquet"
                population.to_parquet(pop_path, index=False)
                raw, probability, net = predict_bundle(bundle, current[columns].to_numpy())
                for index in np.linspace(0, len(current) - 1, 5, dtype=int):
                    scalar = scalar_predict(bundle, current[columns].iloc[index].to_numpy())
                    if (
                        max(
                            abs(scalar[j] - x[index]) for j, x in enumerate([raw, probability, net])
                        )
                        > 1e-10
                    ):
                        raise ValueError("Independent tree traversal differs")
                frame = current[KEYS].copy()
                frame["model"], frame["model_key"] = group, key
                frame["raw_probability"], frame["probability"], frame["predicted_net"] = (
                    raw,
                    probability,
                    net,
                )
                monthly.append(frame)
                metadata.append(
                    {
                        "key": key,
                        "model": group,
                        "sleeve": "small",
                        "features": columns,
                        "training_start": train_start.isoformat(),
                        "training_end": train_end.isoformat(),
                        "calibration_start": cal_start.isoformat(),
                        "calibration_end": cal_end.isoformat(),
                        "forecast_start": month.isoformat(),
                        "fit_rows": len(train),
                        "calibration_rows": len(cal),
                        "model_sha256": sha(path),
                        "population_sha256": sha(pop_path),
                        "training_positive_fraction": float(train.net_label_3bps.gt(0).mean()),
                        "calibration_positive_fraction": float(cal.net_label_3bps.gt(0).mean()),
                    }
                )
                save(output / "model_checkpoint.json", {"completed_bundles": metadata})
                print(
                    {
                        "fitted": key,
                        "train": len(train),
                        "calibration": len(cal),
                        "forecasts": len(current),
                    },
                    flush=True,
                )
            month_frame = pd.concat(monthly, ignore_index=True)
            month_frame.to_parquet(output / "forecasts" / f"{month:%Y%m}.parquet", index=False)
            predictions.append(month_frame)
    forecasts = pd.concat(predictions, ignore_index=True)
    selected = select_predictions(forecasts, plan)
    forecasts.to_parquet(output / "all_predictions.parquet", index=False)
    selected.to_parquet(output / "selected_predictions.parquet", index=False)
    save(
        output / "receipt.json",
        {
            "plan_sha256": sha(plan_path),
            "model_metadata": metadata,
            "predictions_sha256": sha(output / "all_predictions.parquet"),
            "selected_predictions_sha256": sha(output / "selected_predictions.parquet"),
            "common_population_across_groups": True,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "bundles": len(metadata),
            "forecasts": len(forecasts),
            "selected": selected.model.value_counts().to_dict(),
        },
        flush=True,
    )


def evaluate(plan_path, root):
    plan = read(plan_path)
    verify_sources(plan)
    learning, output, panel = root / "learning", root / "evaluation", root / "panel"
    receipt = read(learning / "receipt.json")
    for name, key in [
        ("all_predictions", "predictions_sha256"),
        ("selected_predictions", "selected_predictions_sha256"),
    ]:
        if sha(learning / f"{name}.parquet") != receipt[key]:
            raise ValueError("Frozen forecasts changed")
    output.mkdir(exist_ok=False)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "before_H2_evaluation": True,
            "forecasts_sha256": receipt["predictions_sha256"],
        },
    )
    held = pd.read_parquet(panel / "validation_features.parquet")
    selected = pd.read_parquet(learning / "selected_predictions.parquet")
    forecasts = pd.read_parquet(learning / "all_predictions.parquet")
    calendar = pd.date_range(
        plan["evaluate_start"], plan["end"], freq="D", inclusive="left", tz="UTC"
    )
    results, hashes, increments = [], {}, []
    for cost in plan["extra_cost_bps_per_side"]:
        labels = label_rows(
            held,
            plan["dataset"],
            read(Path(plan["dataset"]) / "receipts.json"),
            pd.Timestamp(plan["end"], tz="UTC"),
            cost,
        )
        full = held[KEYS].merge(labels, on="row_id", validate="one_to_one")
        path = output / f"validation_units_cost_{cost}bps.parquet"
        full.to_parquet(path, index=False)
        hashes[path.name] = sha(path)
        chosen = selected.merge(labels, on="row_id", validate="many_to_one")
        for number, model in enumerate(plan["models"]):
            raw = chosen.loc[chosen.model.eq(model)]
            units = raw.loc[raw.risk_eligible]
            record = block_statistics(
                units,
                units.net_label_3bps.to_numpy(),
                calendar,
                plan,
                plan["random_seed"] + 100 * number + cost,
            )
            record.update(
                model=model,
                sleeve="small",
                extra_cost_bps_per_side=cost,
                frozen_forecast_units=len(raw),
                entry_risk_rejected_units=len(raw) - len(units),
            )
            results.append(record)
        if cost == 3:
            paired = forecasts.merge(
                labels[["row_id", "net_label_3bps", "risk_eligible"]],
                on="row_id",
                validate="many_to_one",
            ).loc[lambda f: f.risk_eligible]
            baseline = paired.loc[paired.model.eq("Price")].set_index("row_id").sort_index()
            for number, model in enumerate(["Flow", "FlowActivity"]):
                other = paired.loc[paired.model.eq(model)].set_index("row_id").sort_index()
                if not baseline.index.equals(other.index):
                    raise ValueError("Ablation population differs")
                target = baseline.net_label_3bps.to_numpy()
                for metric, field, outcome in [
                    ("brier", "probability", target > 0),
                    ("net_mse", "predicted_net", target),
                ]:
                    improvement = (baseline[field].to_numpy() - outcome) ** 2 - (
                        other[field].to_numpy() - outcome
                    ) ** 2
                    row = block_statistics(
                        baseline,
                        improvement,
                        calendar,
                        plan,
                        plan["random_seed"] + 1000 + number * 10 + (metric == "net_mse"),
                    )
                    row["mean_loss_reduction"] = row.pop("mean_net_unit_label")
                    row["fraction_rows_improved"] = row.pop("positive_unit_fraction")
                    row.update(model=model, metric=metric, baseline="Price")
                    increments.append(row)
        print({"evaluated_cost": cost, "unit_paths": len(full)}, flush=True)
    family = results + increments
    if len(family) != 13:
        raise ValueError("Changed preregistered thirteen-test family")
    for row, qvalue in zip(
        family, bh_adjust([r["one_sided_block_pvalue"] for r in family]), strict=True
    ):
        row["unified_BH_qvalue"] = float(qvalue)
    for row in results:
        row["unit_screen_passed"] = bool(
            row["units"] >= plan["minimum_evaluation_units"]
            and row["distinct_days"] >= plan["minimum_distinct_days"]
            and row["units_per_calendar_day"] >= plan["minimum_small_opportunities_per_day"]
            and row["mean_net_unit_label"] is not None
            and row["mean_net_unit_label"] > 0
            and row["positive_unit_fraction"] >= plan["minimum_observed_positive_fraction"]["small"]
            and row["block_ci_95"] is not None
            and row["block_ci_95"][0] > 0
            and row["unified_BH_qvalue"] <= plan["maximum_unified_qvalue"]
        )
    eligible = [
        m
        for m in plan["models"]
        if all(r["unit_screen_passed"] for r in results if r["model"] == m)
    ]
    save(
        output / "result.json",
        {
            "evaluated_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "unit_results": results,
            "paired_loss_improvements": increments,
            "unified_comparisons": 13,
            "cost_label_file_hashes": hashes,
            "native_account_forecast_candidates": eligible,
            "scope": "Common fresh5m absorption events. Retrospective2024;overlapping labels,"
            "not account PNL/winrate/Sharpe.",
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print({"comparisons": 13, "native_candidates": eligible}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "learn", "evaluate"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    {"prepare": prepare, "learn": learn, "evaluate": evaluate}[args.stage](args.plan, args.root)
