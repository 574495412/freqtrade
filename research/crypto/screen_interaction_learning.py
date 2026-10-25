"""Evaluate frozen prequential unit forecasts with day-block uncertainty and unified costs."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.interaction_features import label_rows
from research.crypto.interaction_models import verify_sources


def block_statistics(units, labels, calendar, plan, seed):
    if len(units) == 0:
        return {
            "units": 0,
            "distinct_days": 0,
            "units_per_calendar_day": 0.0,
            "mean_net_unit_label": None,
            "positive_unit_fraction": None,
            "block_ci_95": None,
            "one_sided_block_pvalue": 1.0,
        }
    table = pd.DataFrame({"date": units.date.dt.floor("D"), "label": labels})
    group = table.groupby("date").label.agg(["sum", "count"]).reindex(calendar, fill_value=0)
    observed = float(np.mean(labels))
    totals, counts = group["sum"].to_numpy(), group["count"].to_numpy()
    centered, rng = totals - observed * counts, np.random.default_rng(seed)
    boot, null = [], []
    for _ in range(plan["bootstrap_replicates"]):
        starts = rng.integers(
            0, len(calendar), size=int(np.ceil(len(calendar) / plan["block_days"]))
        )
        indices = ((starts[:, None] + np.arange(plan["block_days"])) % len(calendar)).ravel()[
            : len(calendar)
        ]
        count = counts[indices].sum()
        if count:
            boot.append(totals[indices].sum() / count)
            null.append(centered[indices].sum() / count)
    adequate = (
        len(units) >= plan["minimum_evaluation_units"]
        and int((counts > 0).sum()) >= plan["minimum_distinct_days"]
    )
    return {
        "units": len(units),
        "distinct_days": int((counts > 0).sum()),
        "units_per_calendar_day": len(units) / len(calendar),
        "mean_net_unit_label": observed,
        "positive_unit_fraction": float(np.mean(labels > 0)),
        "block_ci_95": np.quantile(boot, [0.025, 0.975]).tolist() if boot and adequate else None,
        "one_sided_block_pvalue": (1 + int(np.sum(np.asarray(null) >= observed))) / (1 + len(null))
        if null and adequate
        else 1.0,
    }


def bh_adjust(pvalues):
    values, order = np.asarray(pvalues, dtype=float), np.argsort(pvalues)
    ranked = values[order] * len(values) / np.arange(1, len(values) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1].clip(0, 1)
    result = np.empty(len(values))
    result[order] = ranked
    return result


def screen(plan_path, panel, learning, output):
    plan = read(plan_path)
    verify_sources(plan)
    receipt = read(learning / "receipt.json")
    if receipt["plan_sha256"] != sha(plan_path) or output.exists():
        raise ValueError("Forecasts mismatched or evaluation already exists")
    for name, field in [
        ("all_predictions.parquet", "predictions_sha256"),
        ("selected_predictions.parquet", "selected_predictions_sha256"),
    ]:
        if sha(learning / name) != receipt[field]:
            raise ValueError("Frozen forecasts changed")
    output.mkdir(parents=True)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "forecast_receipt_sha256": sha(learning / "receipt.json"),
            "predictions_sha256": receipt["predictions_sha256"],
            "selected_sha256": receipt["selected_predictions_sha256"],
            "before_post_forecast_evaluation": True,
        },
    )
    features = pd.read_parquet(panel / "validation_features.parquet")
    raw_receipts = read(Path(plan["dataset"]) / "receipts.json")
    labels = label_rows(
        features, plan["dataset"], raw_receipts, pd.Timestamp(plan["end"], tz="UTC")
    )
    labels = features[
        ["row_id", "symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]
    ].merge(labels, on="row_id", validate="one_to_one")
    labels.to_parquet(output / "validation_units.parquet", index=False)
    selected = pd.read_parquet(learning / "selected_predictions.parquet").merge(
        labels.drop(columns=["symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]),
        on="row_id",
        validate="many_to_one",
    )
    calendar = pd.date_range(
        pd.Timestamp(plan["evaluate_start"], tz="UTC"),
        pd.Timestamp(plan["end"], tz="UTC"),
        freq="D",
        inclusive="left",
    )
    results = []
    for number, model in enumerate(plan["models"]):
        for index, sleeve in enumerate(["small", "core"]):
            units = selected.loc[selected.model.eq(model) & selected.sleeve.eq(sleeve)]
            for cost in plan["extra_cost_bps_per_side"]:
                net = (
                    units.price_return
                    + units.funding_credit
                    - (plan["fee_per_side"] + cost / 10000) * units.fee_multiplier
                )
                record = block_statistics(
                    units,
                    net.to_numpy(),
                    calendar,
                    plan,
                    plan["random_seed"] + 100 * number + 10 * index + cost,
                )
                record.update(model=model, sleeve=sleeve, extra_cost_bps_per_side=cost)
                results.append(record)
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
    all_predictions = pd.read_parquet(learning / "all_predictions.parquet")
    full = all_predictions.merge(
        labels[["row_id", "net_label_3bps"]], on="row_id", validate="many_to_one"
    )
    diagnostics = []
    for (model, sleeve), group in full.groupby(["model", "sleeve"]):
        positive = group.net_label_3bps.gt(0).to_numpy()
        bins = []
        edges = np.linspace(0, 1, 11)
        for bin_index, lower in enumerate(edges[:-1]):
            chosen = group.loc[
                group.probability.ge(lower)
                & (
                    group.probability.lt(edges[bin_index + 1])
                    if bin_index < 9
                    else group.probability.le(1)
                )
            ]
            bins.append(
                {
                    "lower_probability": float(lower),
                    "rows": len(chosen),
                    "mean_probability": float(chosen.probability.mean()) if len(chosen) else None,
                    "positive_fraction": float(chosen.net_label_3bps.gt(0).mean())
                    if len(chosen)
                    else None,
                }
            )
        diagnostics.append(
            {
                "model": model,
                "sleeve": sleeve,
                "forecast_units": len(group),
                "raw_brier": float(np.mean((group.raw_probability.to_numpy() - positive) ** 2)),
                "calibrated_brier": float(np.mean((group.probability.to_numpy() - positive) ** 2)),
                "net_label_mse": float(np.mean((group.predicted_net - group.net_label_3bps) ** 2)),
                "probability_bins": bins,
            }
        )
    eligible = [
        model
        for model in plan["models"]
        if all(r["unit_screen_passed"] for r in results if r["model"] == model)
    ]
    for name, field in [
        ("all_predictions.parquet", "predictions_sha256"),
        ("selected_predictions.parquet", "selected_predictions_sha256"),
    ]:
        if sha(learning / name) != receipt[field]:
            raise ValueError("Model selection changed during outcome evaluation")
    save(
        output / "result.json",
        {
            "evaluated_utc": datetime.now(UTC).isoformat(),
            "plan_sha256": sha(plan_path),
            "unit_results": results,
            "calibration_diagnostics": diagnostics,
            "unified_comparisons": len(results),
            "forecast_candidates_eligible_for_native_account": eligible,
            "validation_units_sha256": sha(output / "validation_units.parquet"),
            "native_source_files": len(source_identity()),
            "economic_accounts_computed": False,
            "scope": "Fixed-horizon unit labels, not account ROI/winrate/Sharpe. Three model "
            "policies on previously used 2024 history; monthly policy learns only from matured "
            "past outcomes. Seven-day circular block bootstrap and unified BH across 18 cases. "
            "Prior project explorations are not covered by that BH family. No stops, liquidation, "
            "capacity, entry rounding or reinvestment modeled at this screen.",
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "comparisons": len(results),
            "native_account_forecast_candidates": eligible,
            "selected_units": len(selected),
            "account_profits_computed": False,
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--learning", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    screen(args.plan, args.panel, args.learning, args.output)
