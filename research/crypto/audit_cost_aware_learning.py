"""Independent Decimal horizon labels, augmented ridge solve and forecast arithmetic."""

import argparse
import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


def check(run, output):  # noqa: C901 - independent label, model and forecast evidence
    if output.exists():
        raise ValueError("Preserve existing learning audit")
    for name, expected in read(run / "receipts.json").items():
        if sha(run / name) != expected:
            raise ValueError("Learning artifact identity changed")
    plan = read(run / "protocol.json")
    root, cut = Path(plan["dataset"]), pd.Timestamp(plan["fit_end"])
    train = pd.read_parquet(run / "training_units.parquet")
    test = pd.read_parquet(run / "heldout_feature_predictions.parquet")
    if not (train.date + pd.Timedelta(minutes=1445)).lt(cut).all():
        raise ValueError("Training fixed maturity crosses model cutoff")
    if not train.label_known_at_ms.lt(cut.value // 1_000_000).all() or not test.date.ge(cut).all():
        raise ValueError("Training and forecast clocks overlap")
    if "unit_net_horizon_label" in test.columns:
        raise ValueError("Heldout outcome leaked into forecast artifact")
    models, label_errors, coefficient_errors, forecast_errors, gradients = (
        read(run / "models.json"),
        [],
        [],
        [],
        [],
    )
    checked_labels, checked_forecasts = 0, 0
    for sleeve in ["small", "core"]:
        for side in [1, -1]:
            key = f"{sleeve}_{'long' if side == 1 else 'short'}"
            population = train.loc[
                train.opportunity_sleeve.eq(sleeve) & train.opportunity_side.eq(side)
            ]
            sample = population.sample(min(25, len(population)), random_state=20261024)
            for symbol, group in sample.groupby("symbol"):
                stem = symbol.removesuffix("USDT") + "_USDT_USDT"
                prices = pd.read_feather(
                    root / f"candles/futures/{stem}-5m-futures.feather"
                ).set_index("date")
                marks = (
                    pd.read_feather(root / f"candles/futures/{stem}-1h-mark.feather")
                    .set_index("date")
                    .open
                )
                funding = pd.read_parquet(root / f"{symbol}_funding_events.parquet")
                for row in group.itertuples():
                    entry_time = pd.to_datetime(row.entry_at_ms, unit="ms", utc=True)
                    end_time = pd.to_datetime(row.label_known_at_ms, unit="ms", utc=True)
                    if entry_time != row.feature_open + pd.Timedelta(minutes=10):
                        raise ValueError("Forecast entry latency changed")
                    if end_time - entry_time != pd.Timedelta(
                        minutes=5 * plan["forecast_bars"][sleeve]
                    ):
                        raise ValueError("Fixed forecast horizon changed")
                    entry, exit_ = (
                        Decimal(str(prices.open.loc[entry_time])),
                        Decimal(str(prices.open.loc[end_time])),
                    )
                    flows = funding.loc[
                        (funding.calc_time >= row.entry_at_ms)
                        & (funding.calc_time < row.label_known_at_ms)
                    ]
                    cash = sum(
                        (
                            Decimal(str(f.funding_rate))
                            * Decimal(str(marks.loc[f.date.floor("h")]))
                            for f in flows.itertuples()
                        ),
                        Decimal(0),
                    )
                    net = (
                        side * (exit_ / entry - 1)
                        - Decimal(str(plan["fee_per_side"])) * (1 + exit_ / entry)
                        - side * cash / entry
                    )
                    error = abs(float(net) - row.unit_net_horizon_label)
                    if error > 1e-10 or int(net > 0) != row.horizon_positive_label:
                        raise ValueError("Independent Decimal horizon label differs")
                    label_errors.append(error)
                    checked_labels += 1
            model = models[key]
            if model["status"] != "FITTED":
                continue
            columns = model["features"]
            x, y = population[columns].to_numpy(), population.unit_net_horizon_label.to_numpy()
            means = np.array([math.fsum(x[:, i]) / len(x) for i in range(x.shape[1])])
            deviations = np.sqrt(
                np.array([math.fsum((x[:, i] - means[i]) ** 2) / len(x) for i in range(x.shape[1])])
            )
            deviations = np.where(deviations > 0, deviations, 1)
            if not np.allclose(means, model["mean"], atol=1e-10, rtol=1e-10) or not np.allclose(
                deviations, model["deviation"], atol=1e-10, rtol=1e-10
            ):
                raise ValueError("Standardization was not fitted only on eligible training units")
            z = np.column_stack([np.ones(len(x)), (x - means) / deviations])
            penalty = np.eye(z.shape[1]) * math.sqrt(plan["penalty"])
            penalty[0, 0] = 0
            augmented = np.vstack([z / math.sqrt(len(z)), penalty])
            response = np.r_[y / math.sqrt(len(z)), np.zeros(z.shape[1])]
            linear = np.linalg.lstsq(augmented, response, rcond=None)[0]
            coefficient_errors.append(float(np.abs(linear - np.asarray(model["linear"])).max()))
            scores = z @ np.asarray(model["logistic"])
            probabilities = 1 / (1 + np.exp(-np.clip(scores, -700, 700)))
            gradient = z.T @ (probabilities - population.horizon_positive_label.to_numpy()) / len(z)
            gradient[1:] += plan["penalty"] * np.asarray(model["logistic"])[1:]
            gradients.append(float(np.abs(gradient).max()))
            forecasts = test.loc[
                test.opportunity_sleeve.eq(sleeve)
                & test.opportunity_side.eq(side)
                & test.model_ready
            ]
            sample = forecasts.sample(min(20, len(forecasts)), random_state=20261024)
            for row in sample.itertuples():
                values = [getattr(row, col) for col in columns]
                scaled = [
                    (v - m) / d
                    for v, m, d in zip(values, model["mean"], model["deviation"], strict=True)
                ]
                if max(map(abs, scaled)) > plan["maximum_absolute_standardized_feature"] + 1e-10:
                    raise ValueError("Out-of-range observation admitted")
                net = model["linear"][0] + math.fsum(
                    v * w for v, w in zip(scaled, model["linear"][1:], strict=True)
                )
                score = model["logistic"][0] + math.fsum(
                    v * w for v, w in zip(scaled, model["logistic"][1:], strict=True)
                )
                probability = 1 / (1 + math.exp(-score))
                forecast_errors += [
                    abs(net - row.predicted_net_horizon_return),
                    abs(probability - row.predicted_horizon_positive_probability),
                ]
                checked_forecasts += 1
    if (
        max(coefficient_errors, default=0) > 1e-8
        or max(gradients, default=0) > 5e-5
        or max(forecast_errors, default=0) > 1e-10
    ):
        raise ValueError("Independent model arithmetic or optimizer gradient differs")
    result = {
        "passed": True,
        "decimal_labels_checked": checked_labels,
        "forecasts_checked": checked_forecasts,
        "maximum_decimal_label_error": max(label_errors, default=0),
        "maximum_augmented_ridge_coefficient_error": max(coefficient_errors, default=0),
        "maximum_logistic_gradient": max(gradients, default=0),
        "maximum_forecast_error": max(forecast_errors, default=0),
        "training_cutoff_utc": cut.isoformat(),
        "heldout_outcomes_read": False,
        "learning_receipts_sha256": sha(run / "receipts.json"),
        "audit_source_sha256": sha(Path(__file__)),
        "scope": (
            "Training labels and forecasting arithmetic only; "
            "not account profit or live execution proof."
        ),
    }
    save(output, result)
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    check(args.run, args.output)
