"""Evaluate frozen first-hit forecasts; recompute the whole path at every registered cost."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.interaction_models import verify_sources
from research.crypto.intrabar_small_paths import label_rows
from research.crypto.screen_interaction_learning import bh_adjust, block_statistics


def screen(plan_path, panel, learning, output):
    plan = read(plan_path)
    verify_sources(plan)
    receipt = read(learning / "receipt.json")
    if receipt["plan_sha256"] != sha(plan_path) or output.exists():
        raise ValueError("Forecast identity mismatch or existing evaluation")
    for name, key in [
        ("all_predictions.parquet", "predictions_sha256"),
        ("selected_predictions.parquet", "selected_predictions_sha256"),
    ]:
        if sha(learning / name) != receipt[key]:
            raise ValueError("Frozen forecasts changed")
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "forecast_receipt_sha256": sha(learning / "receipt.json"),
            "selected_sha256": receipt["selected_predictions_sha256"],
            "before_new_H2_path_evaluation": True,
        },
    )
    features = pd.read_parquet(panel / "validation_features.parquet")
    selected = pd.read_parquet(learning / "selected_predictions.parquet")
    calendar = pd.date_range(
        plan["evaluate_start"], plan["end"], freq="D", inclusive="left", tz="UTC"
    )
    raw_receipts = read(Path(plan["dataset"]) / "receipts.json")
    keys = ["row_id", "symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]
    results, hashes, primary = [], {}, None
    for cost in plan["extra_cost_bps_per_side"]:
        labels = label_rows(
            features, plan["dataset"], raw_receipts, pd.Timestamp(plan["end"], tz="UTC"), cost
        )
        full = features[keys].merge(labels, on="row_id", validate="one_to_one")
        path = output / f"validation_units_cost_{cost}bps.parquet"
        full.to_parquet(path, index=False)
        hashes[path.name] = sha(path)
        if cost == 3:
            primary = full
        chosen = selected.merge(labels, on="row_id", validate="many_to_one")
        for number, model in enumerate(plan["models"]):
            for index, sleeve in enumerate(["small"]):
                raw = chosen.loc[chosen.model.eq(model) & chosen.sleeve.eq(sleeve)]
                units = raw.loc[raw.risk_eligible]
                record = block_statistics(
                    units,
                    units.net_label_3bps.to_numpy(),
                    calendar,
                    plan,
                    plan["random_seed"] + 100 * number + 10 * index + cost,
                )
                record.update(
                    model=model,
                    sleeve=sleeve,
                    extra_cost_bps_per_side=cost,
                    frozen_forecast_units=len(raw),
                    entry_risk_rejected_units=len(raw) - len(units),
                )
                results.append(record)
        print({"cost_paths_evaluated": cost, "units": len(full)}, flush=True)
    adjusted = bh_adjust([r["one_sided_block_pvalue"] for r in results])
    for record, qvalue in zip(results, adjusted, strict=True):
        record["unified_BH_qvalue"] = float(qvalue)
        record["unit_screen_passed"] = bool(
            record["units"] >= plan["minimum_evaluation_units"]
            and record["distinct_days"] >= plan["minimum_distinct_days"]
            and record["mean_net_unit_label"] is not None
            and record["mean_net_unit_label"] > 0
            and record["positive_unit_fraction"]
            >= plan["minimum_observed_positive_fraction"][record["sleeve"]]
            and record["block_ci_95"] is not None
            and record["block_ci_95"][0] > 0
            and qvalue <= plan["maximum_unified_qvalue"]
            and (
                record["sleeve"] != "small"
                or record["units_per_calendar_day"] >= plan["minimum_small_opportunities_per_day"]
            )
        )
    diagnostics = []
    predictions = pd.read_parquet(learning / "all_predictions.parquet")
    full = predictions.merge(
        primary[["row_id", "net_label_3bps", "risk_eligible"]], on="row_id", validate="many_to_one"
    )
    full = full.loc[full.risk_eligible]
    for (model, sleeve), group in full.groupby(["model", "sleeve"]):
        positive = group.net_label_3bps.gt(0).to_numpy()
        diagnostics.append(
            {
                "model": model,
                "sleeve": sleeve,
                "forecast_units": len(group),
                "maximum_calibrated_probability": float(group.probability.max()),
                "calibrated_brier": float(np.mean((group.probability.to_numpy() - positive) ** 2)),
                "mean_net_unit_label": float(group.net_label_3bps.mean()),
            }
        )
    eligible = [
        model
        for model in plan["models"]
        if all(r["unit_screen_passed"] for r in results if r["model"] == model)
    ]
    result = {
        "evaluated_utc": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(plan_path),
        "unit_results": results,
        "calibration_diagnostics": diagnostics,
        "unified_comparisons": len(results),
        "cost_label_file_hashes": hashes,
        "forecast_candidates_eligible_for_native_account": eligible,
        "native_source_files": len(source_identity()),
        "economic_accounts_computed": False,
        "screened_sleeves": ["small"],
        "frozen_core_control": (
            "Previously frozen BoostMonthly core predictions shared unchanged "
            "across all3 small policies. Its earlier unit verdict remains "
            "failed; the3bps native component result was positive but weak."
        ),
        "joint_strategy_qualified": False,
        "scope": (
            "Nine new small-component comparisons only:3policies times3costs. Small uses "
            "native intrabar ROI for a net.25R target, regular stop before ROI, and4h cap. "
            "Core checkpoint predictions are frozen controls, not newly fitted or "
            "independently rescreened. Labels overlap and are not account PNL, winrate or "
            "Sharpe. Conditional native candidates must still pass joint capital accounts, "
            "risk-adjusted metrics and independent forward evidence.2024 is previously "
            "observed history, not independent OOS."
        ),
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output / "result.json", result)
    print({"comparisons": len(results), "native_account_forecast_candidates": eligible}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "panel", "learning", "output"]:
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    screen(args.plan, args.panel, args.learning, args.output)
