"""Native path replay, raw Decimal settlement and independent chronological model audit."""

import argparse
import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.activity_absorption_features import GROUPS
from research.crypto.first_hit_paths import market_bundle
from research.crypto.interaction_models import enable_runtime, scalar_predict, verify_sources
from research.crypto.replay_intrabar_small import replay


def audit_units(plan, units, cost=3):
    available = units.loc[units.risk_eligible].reset_index(drop=True)
    indices = []
    for _, group in available.groupby(["sleeve", "side", "exit_reason"]):
        indices.extend(group.index[[0, -1]].tolist())
    indices.extend(np.linspace(0, len(available) - 1, 120, dtype=int).tolist())
    indices = list(dict.fromkeys(indices))[:120]
    errors, reasons = {}, {}
    for unit in available.iloc[indices].itertuples(index=False):
        price, funding, mark = market_bundle(
            plan["dataset"], plan["data_receipts_sha256"], unit.symbol
        )
        native = replay(unit, price, funding, mark, cost)
        if native["exit_time"] != unit.exit_time or native["exit_reason"] != unit.exit_reason:
            raise ValueError(("Native path event differs", unit.row_id, native, unit.exit_reason))
        for field, actual, expected in [
            ("net_label", unit.net_label_3bps, native["net_label"]),
            ("funding_credit", unit.funding_credit, native["funding_credit"]),
            ("exit_price", unit.exit_price, native["exit_price"]),
            ("locked_stop", unit.locked_stop, native["locked_stop"]),
            ("target_roi", unit.target_roi, native["target_roi"]),
        ]:
            error = abs(actual - expected)
            errors[field] = max(errors.get(field, 0), error)
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-11):
                raise ValueError((field, unit.row_id, actual, expected))
        reasons[unit.exit_reason] = reasons.get(unit.exit_reason, 0) + 1
    return {
        "units_checked": len(indices),
        "exit_reason_samples": reasons,
        "maximum_absolute_errors": errors,
    }


def audit(plan_path, panel, learning=None, evaluation=None):  # noqa: C901 - phase-specific audit
    plan = read(plan_path)
    verify_sources(plan)
    prepared = read(panel / "receipt.json")
    if prepared["plan_sha256"] != sha(plan_path):
        raise ValueError("Prepared protocol changed")
    for name, digest in prepared["file_hashes"].items():
        if sha(panel / name) != digest:
            raise ValueError("Prepared features or path labels changed")
    if read(panel / "freeze.json")["source_identity"] != source_identity():
        raise ValueError("Native framework changed")
    h1 = pd.read_parquet(panel / "h1_units.parquet")
    result = {
        "H1_raw_units": audit_units(plan, h1),
        "passed": True,
        "native_source_files_unchanged": len(source_identity()),
        "plan_sha256": sha(plan_path),
        "audit_source_sha256": sha(Path(__file__)),
        "orders_enabled": False,
        "production_eligible": False,
    }
    if learning is not None:
        enable_runtime(plan)
        import joblib

        receipt = read(learning / "receipt.json")
        if receipt["plan_sha256"] != sha(plan_path):
            raise ValueError("Forecast protocol mismatch")
        predictions = pd.read_parquet(learning / "all_predictions.parquet")
        for name, key in [
            ("all_predictions.parquet", "predictions_sha256"),
            ("selected_predictions.parquet", "selected_predictions_sha256"),
        ]:
            if sha(learning / name) != receipt[key]:
                raise ValueError("Frozen predictions changed")
        features = pd.read_parquet(panel / "all_features.parquet").set_index("row_id")
        cache = pd.concat(
            [
                h1[["row_id", "risk_eligible", "net_label_3bps", "label_known_at"]],
                *[
                    pd.read_parquet(p)[
                        ["row_id", "risk_eligible", "net_label_3bps", "label_known_at"]
                    ]
                    for p in sorted(learning.glob("admitted_past_units_*.parquet"))
                ],
            ],
            ignore_index=True,
        )
        if cache.row_id.duplicated().any():
            raise ValueError("Outcome admitted more than once")
        cache = cache.set_index("row_id")
        errors, populations = [], []
        for meta in receipt["model_metadata"]:
            if meta["features"] != GROUPS[meta["model"]]:
                raise ValueError("Nested feature specification changed")
            key = meta["key"]
            model_path, population_path = (
                learning / "models" / f"{key}.joblib",
                learning / "models" / f"{key}_population.parquet",
            )
            if (
                sha(model_path) != meta["model_sha256"]
                or sha(population_path) != meta["population_sha256"]
            ):
                raise ValueError("Model or admitted population changed")
            actual = pd.read_parquet(population_path)
            if actual.row_id.duplicated().any():
                raise ValueError("Overlapping fit and calibration rows")
            for role, prefix in [("train", "training"), ("calibration", "calibration")]:
                lower, upper = (
                    pd.Timestamp(meta[f"{prefix}_start"]),
                    pd.Timestamp(meta[f"{prefix}_end"]),
                )
                expected = features.loc[
                    features.sleeve.eq(meta["sleeve"])
                    & features.date.ge(lower)
                    & (features.date + pd.Timedelta(days=1, minutes=5)).lt(upper)
                ].index
                expected = cache.loc[expected].loc[lambda x: x.risk_eligible].index
                observed = actual.loc[actual.role.eq(role)]
                if (
                    set(expected) != set(observed.row_id)
                    or not observed.label_known_at.lt(upper).all()
                ):
                    raise ValueError("Training/calibration is not the registered causal population")
                raw = cache.loc[observed.row_id]
                if not np.allclose(raw.net_label_3bps, observed.net_label_3bps, rtol=0, atol=1e-14):
                    raise ValueError("Fitted targets differ from admitted path outcomes")
            bundle = joblib.load(model_path)
            forecasts = predictions.loc[predictions.model_key.eq(key)]
            sample = forecasts.iloc[np.linspace(0, len(forecasts) - 1, 6, dtype=int)]
            for row in sample.itertuples(index=False):
                raw, probability, net = scalar_predict(
                    bundle, features.loc[row.row_id, GROUPS[meta["model"]]].to_numpy(dtype=float)
                )
                error = max(
                    abs(raw - row.raw_probability),
                    abs(probability - row.probability),
                    abs(net - row.predicted_net),
                )
                if error > 1e-10:
                    raise ValueError("Independent scalar prediction differs")
                errors.append(error)
            populations.append(
                {"key": key, "fit_and_calibration_rows": len(actual), "passed": True}
            )
        result.update(
            model_populations_checked=populations,
            independent_prediction_samples=len(errors),
            maximum_scalar_prediction_error=max(errors),
            prediction_hashes_unchanged=True,
        )
    if evaluation is not None:
        record = read(evaluation / "result.json")
        held = pd.read_parquet(panel / "validation_features.parquet")
        audits = []
        for cost in plan["extra_cost_bps_per_side"]:
            path = evaluation / f"validation_units_cost_{cost}bps.parquet"
            if sha(path) != record["cost_label_file_hashes"][path.name]:
                raise ValueError("Cost-specific full paths changed")
            labels = pd.read_parquet(path)
            units = held.merge(
                labels.drop(
                    columns=["symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]
                ),
                on="row_id",
                validate="one_to_one",
            )
            audits.append({"cost": cost, **audit_units(plan, units, cost)})
        primary = pd.read_parquet(evaluation / "validation_units_cost_3bps.parquet")
        observed = predictions.merge(
            primary[["row_id", "net_label_3bps", "risk_eligible"]],
            on="row_id",
            validate="many_to_one",
        ).loc[lambda f: f.risk_eligible]
        baseline = observed.loc[observed.model.eq("Price")].set_index("row_id").sort_index()
        checked = []
        for increment in record["paired_loss_improvements"]:
            other = (
                observed.loc[observed.model.eq(increment["model"])].set_index("row_id").sort_index()
            )
            if not baseline.index.equals(other.index):
                raise ValueError("Independent paired population differs")
            field = "probability" if increment["metric"] == "brier" else "predicted_net"
            total = Decimal(0)
            for index in baseline.index:
                truth = baseline.loc[index, "net_label_3bps"]
                target = Decimal(int(truth > 0)) if field == "probability" else Decimal(str(truth))
                first, second = (
                    Decimal(str(baseline.loc[index, field])),
                    Decimal(str(other.loc[index, field])),
                )
                total += (first - target) ** 2 - (second - target) ** 2
            expected = float(total / len(baseline))
            if not math.isclose(
                expected, increment["mean_loss_reduction"], rel_tol=1e-9, abs_tol=1e-13
            ):
                raise ValueError("Decimal paired prediction-loss reduction differs")
            checked.append(
                {
                    "model": increment["model"],
                    "metric": increment["metric"],
                    "paired_rows": len(baseline),
                    "mean_loss_reduction": expected,
                }
            )
        result["independent_paired_loss_checks"] = checked
        family = record["unit_results"] + record["paired_loss_improvements"]
        if len(family) != 13:
            raise ValueError("Changed comparison family")
        ranked = sorted(family, key=lambda x: x["one_sided_block_pvalue"])
        ceiling = 1.0
        for rank in reversed(range(13)):
            row = ranked[rank]
            ceiling = min(ceiling, row["one_sided_block_pvalue"] * 13 / (rank + 1))
            if abs(ceiling - row["unified_BH_qvalue"]) > 1e-12:
                raise ValueError("Unified BH adjustment differs")
        result.update(H2_cost_path_audits=audits, unified_comparisons_checked=13)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "panel", "learning", "evaluation", "output"]:
        parser.add_argument(f"--{name}", type=Path, required=name in {"plan", "panel", "output"})
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous audit")
    result = audit(args.plan, args.panel, args.learning, args.evaluation)
    save(args.output, result)
    print(
        {
            "passed": result["passed"],
            "H1_units": result["H1_raw_units"]["units_checked"],
            "forecast_samples": result.get("independent_prediction_samples"),
        }
    )
