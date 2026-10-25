"""Fit frozen combinations, freeze predictions, then screen non-account outcomes."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.conditional_factors import expanded_groups
from research.crypto.conditional_learning import fit_model, mature_rows, predict


def period_rows(panel, start, end, maximum_bars):
    dates = pd.DatetimeIndex(panel.date)
    # Only deterministic calendar information determines prediction eligibility.
    return mature_rows(
        dates,
        (dates + pd.Timedelta(minutes=10 + 5 * maximum_bars)).as_unit("ms").asi8,
        start,
        end,
        maximum_bars,
    )


def choose_proposals(predictions, plan, sleeve):
    rules = plan["signal_selection"]
    valid = predictions.loc[
        predictions.predicted_win_probability.ge(rules[f"{sleeve}_minimum_win_probability"])
        & predictions.predicted_net_return.ge(rules["minimum_predicted_net_return"])
    ]
    return (
        valid.sort_values(
            ["date", "predicted_net_return", "symbol", "side"],
            ascending=[True, False, True, True],
            kind="stable",
        )
        .drop_duplicates("date", keep="first")
        .reset_index(drop=True)
    )


def summary(frame):
    if frame.empty:
        return {"signals": 0, "actual_net_win_rate": None, "mean_unit_net_label": None}
    return {
        "signals": len(frame),
        "actual_net_win_rate": float(frame.net_win_label.mean()),
        "mean_unit_net_label": float(frame.net_return_label.mean()),
        "mean_predicted_probability": float(frame.predicted_win_probability.mean()),
        "mean_predicted_net_label": float(frame.predicted_net_return.mean()),
        "distinct_signal_days": int(frame.date.dt.floor("D").nunique()),
        "overlapping_labels_are_not_account_pnl": True,
    }


def prediction_scores(frame, training_rate):
    return {
        "rows": len(frame),
        "brier": float(np.square(frame.predicted_win_probability - frame.net_win_label).mean()),
        "train_rate_constant_brier": float(np.square(training_rate - frame.net_win_label).mean()),
        "net_label_mse": float(
            np.square(frame.predicted_net_return - frame.net_return_label).mean()
        ),
        "cash_zero_label_mse": float(np.square(frame.net_return_label).mean()),
    }


def read_period(path, start, end):
    return pd.read_parquet(
        path,
        filters=[
            ("date", ">=", pd.Timestamp(start, tz="UTC")),
            ("date", "<", pd.Timestamp(end, tz="UTC")),
        ],
    )


def screen(plan_path, panel_path, output):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve prior screen")
    plan = read(plan_path)
    receipts = read(panel_path / "receipts.json")
    for name, expected in receipts.items():
        if sha(panel_path / name) != expected:
            raise ValueError(f"Panel input changed: {name}")
    if read(panel_path / "input_freeze.json")["plan_sha256"] != sha(plan_path):
        raise ValueError("Panel plan identity mismatch")
    output.mkdir(parents=True)
    sources = output / "sources"
    sources.mkdir()
    for name in [
        "screen_conditional_factors.py",
        "conditional_learning.py",
        "conditional_factors.py",
    ]:
        path = Path(__file__).with_name(name)
        (sources / name).write_bytes(path.read_bytes())
        if name != "screen_conditional_factors.py" and sha(path) != sha(
            panel_path / "sources" / name
        ):
            raise ValueError("Panel and fitting implementation differ")
    save(output / "plan.json", plan)
    save(
        output / "input_freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "panel_receipts_sha256": sha(panel_path / "receipts.json"),
            "source_sha256": {p.name: sha(p) for p in sources.iterdir()},
            "model_selection_or_parameter_search": False,
            "orders_enabled": False,
        },
    )
    groups = expanded_groups(plan)
    common_columns = list(dict.fromkeys(c for columns in groups.values() for c in columns))
    panels = {}
    for period in ["fit", "selection"]:
        start, end = plan[f"{period}_start"], plan[f"{period}_end"]
        panels[period] = pd.concat(
            [read_period(panel_path / f"{s}.parquet", start, end) for s in plan["symbols"]],
            ignore_index=True,
        )
    coverage = {
        period: {
            "total_rows": len(panel),
            "common_complete_rows": int(np.isfinite(panel[common_columns]).all(axis=1).sum()),
            "own_complete_rows": {
                group: int(np.isfinite(panel[columns]).all(axis=1).sum())
                for group, columns in groups.items()
            },
        }
        for period, panel in panels.items()
    }
    save(output / "population_coverage.json", coverage)
    models = {}
    for sleeve in ["small", "core"]:
        horizon = plan["labels"][sleeve]["maximum_bars"]
        for side in ["long", "short"]:
            labels = pd.concat(
                [
                    read_period(
                        panel_path / "labels" / f"{symbol}_{sleeve}_{side}.parquet",
                        plan["fit_start"],
                        plan["fit_end"],
                    ).assign(symbol=symbol)
                    for symbol in plan["symbols"]
                ],
                ignore_index=True,
            )
            train = panels["fit"].merge(labels, on=["date", "symbol"], validate="one_to_one")
            valid = (
                np.isfinite(train[common_columns]).all(axis=1)
                & mature_rows(
                    train.date,
                    train.label_known_at_ms,
                    plan["fit_start"],
                    plan["fit_end"],
                    horizon,
                )
                & np.isfinite(train.net_return_label)
                & np.isfinite(train.net_win_label)
            )
            train = train.loc[valid]
            for group, columns in groups.items():
                key = f"{group}_{sleeve}_{side}"
                models[key] = fit_model(
                    train[columns],
                    train.net_return_label,
                    train.net_win_label,
                    plan["model"]["lambda"],
                    plan["model"]["logistic_max_iterations"],
                )
                models[key]["features"] = columns
                print(
                    {"fit": key, "rows": len(train), "iterations": models[key]["iterations"]},
                    flush=True,
                )
    save(output / "models.json", models)
    save(
        output / "model_freeze.json",
        {"models_sha256": sha(output / "models.json"), "selection_outcomes_read": False},
    )
    prediction_dir = output / "predictions"
    prediction_dir.mkdir()
    for group, columns in groups.items():
        for sleeve in ["small", "core"]:
            panel = panels["selection"]
            valid = np.isfinite(panel[common_columns]).all(axis=1) & period_rows(
                panel,
                plan["selection_start"],
                plan["selection_end"],
                plan["labels"][sleeve]["maximum_bars"],
            )
            evaluation = panel.loc[valid]
            sides = []
            for side in ["long", "short"]:
                probabilities, returns = predict(
                    models[f"{group}_{sleeve}_{side}"], evaluation[columns]
                )
                sides.append(
                    evaluation[["date", "symbol"]].assign(
                        side=side,
                        predicted_win_probability=probabilities,
                        predicted_net_return=returns,
                    )
                )
            predictions = pd.concat(sides, ignore_index=True)
            predictions.to_parquet(prediction_dir / f"{group}_{sleeve}.parquet", index=False)
            choose_proposals(predictions, plan, sleeve).to_parquet(
                prediction_dir / f"{group}_{sleeve}_proposals.parquet", index=False
            )
    frozen_predictions = {p.name: sha(p) for p in sorted(prediction_dir.iterdir())}
    save(
        output / "prediction_freeze.json",
        {"sha256": frozen_predictions, "selection_outcomes_read": False},
    )
    # No selection outcome is loaded until every model and proposal is frozen.
    results, all_scores = {}, {}
    for sleeve in ["small", "core"]:
        labels = pd.concat(
            [
                read_period(
                    panel_path / "labels" / f"{symbol}_{sleeve}_{side}.parquet",
                    plan["selection_start"],
                    plan["selection_end"],
                ).assign(symbol=symbol, side=side)
                for symbol in plan["symbols"]
                for side in ["long", "short"]
            ],
            ignore_index=True,
        )
        for group in groups:
            key = f"{group}_{sleeve}"
            predictions = pd.read_parquet(prediction_dir / f"{key}.parquet")
            outcome = predictions.merge(
                labels, on=["date", "symbol", "side"], validate="one_to_one"
            )
            if len(outcome) != len(predictions) or not np.isfinite(outcome.net_return_label).all():
                raise ValueError("Prediction population has unknown selection labels")
            scores = {}
            for side in ["long", "short"]:
                scores[side] = prediction_scores(
                    outcome.loc[outcome.side.eq(side)],
                    models[f"{group}_{sleeve}_{side}"]["training_win_rate"],
                )
            all_scores[key] = scores
            proposals = pd.read_parquet(prediction_dir / f"{key}_proposals.parquet")
            evaluated = proposals.merge(
                labels, on=["date", "symbol", "side"], validate="one_to_one"
            )
            results.setdefault(group, {})[sleeve] = summary(evaluated)
            evaluated.to_parquet(output / f"{key}_screened_labels.parquet", index=False)
    increments = []
    for earlier, later in zip(list(groups)[:-1], list(groups)[1:], strict=True):
        for sleeve in ["small", "core"]:
            for side in ["long", "short"]:
                old, new = (
                    all_scores[f"{earlier}_{sleeve}"][side],
                    all_scores[f"{later}_{sleeve}"][side],
                )
                if old["rows"] != new["rows"]:
                    raise ValueError("Comparison population mismatch")
                increments.append(
                    {
                        "from": earlier,
                        "to": later,
                        "sleeve": sleeve,
                        "side": side,
                        "rows": old["rows"],
                        "brier_delta": new["brier"] - old["brier"],
                        "net_label_mse_delta": new["net_label_mse"] - old["net_label_mse"],
                        "negative_delta_is_better": True,
                        "statistical_significance_claim": False,
                    }
                )
    eligible = []
    for group, sleeves in results.items():
        passed = all(
            result["signals"] >= plan["prediction_screen"]["minimum_signals_per_sleeve"]
            and result["actual_net_win_rate"]
            >= plan["prediction_screen"][f"net_win_rate_{sleeve}_at_least"]
            and result["mean_unit_net_label"] > 0
            for sleeve, result in sleeves.items()
        )
        sleeves["prediction_screen_passed"] = passed
        if passed:
            eligible.append(group)
    save(
        output / "scores.json",
        {"models": all_scores, "paired_common_population_increments": increments},
    )
    save(
        output / "screen.json",
        {
            "groups": results,
            "cash": {"account_return": 0.0, "trades": 0, "qualifies": False},
            "label_metrics_are_not_account_returns": True,
        },
    )
    save(
        output / "admission.json",
        {
            "prediction_eligible": eligible,
            "production_eligible": False,
            "orders_enabled": False,
            "reserved_test_outcomes_read": False,
            "next": "native_selection_accounts" if eligible else "new_hypothesis_required",
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print({"prediction_eligible": eligible, "production_eligible": False}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    screen(args.plan.absolute(), args.panel.absolute(), args.output.absolute())
