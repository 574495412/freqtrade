"""Fit on past mature units, then write outcome-free chronological entry predictions."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.conditional_learning import fit_model, predict
from research.crypto.cost_aware_residual import (
    FEATURES,
    feature_rows,
    horizon_labels,
    maturity_mask,
)


def build(protocol_path, output):  # noqa: C901 - ordered source, label, fit and forecast stages
    protocol = read(protocol_path)
    if output.exists():
        raise ValueError("Preserve existing learning run")
    for path, expected in protocol["source_hashes"].items():
        if sha(Path(path)) != expected:
            raise ValueError("Registered learning source changed")
    dataset, panel = Path(protocol["dataset"]), Path(protocol["residual_panel"])
    if sha(dataset / "receipts.json") != protocol["dataset_receipts_sha256"]:
        raise ValueError("Original data identity changed")
    if sha(panel / "receipt.json") != protocol["residual_receipt_sha256"]:
        raise ValueError("Residual input identity changed")
    data_receipts, residual_receipts = read(dataset / "receipts.json"), read(panel / "receipt.json")
    start, cut, end = [pd.Timestamp(protocol[k]) for k in ["fit_start", "fit_end", "evaluate_end"]]

    def original(name):
        path = dataset / name
        if sha(path) != data_receipts[name]:
            raise ValueError(f"Receipted source changed: {name}")
        return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_feather(path)

    def price(symbol):
        name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        frame = original(name)
        frame = frame.loc[(frame.date >= pd.Timestamp("2023-12-01", tz="UTC")) & (frame.date < end)]
        frame = frame.set_index("date")
        if (
            frame.index.has_duplicates
            or not frame.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
        ):
            raise ValueError("Missing or duplicated input price clock")
        return frame

    btc = price("BTCUSDT")
    all_features, all_training = [], []
    for symbol in protocol["symbols"]:
        path = panel / f"{symbol}.parquet"
        if sha(path) != residual_receipts["panel_hashes"][symbol]:
            raise ValueError("Residual feature panel changed")
        source = pd.read_parquet(path)
        source = source.loc[(source.date >= start) & (source.date < end)]
        candles = price(symbol)
        rows = feature_rows(source, candles, btc)
        rows["symbol"] = symbol
        all_features.append(
            rows[
                [
                    "symbol",
                    "date",
                    "feature_open",
                    "opportunity_side",
                    "opportunity_sleeve",
                    "finite_features",
                    *FEATURES,
                ]
            ]
        )
        fit = rows.loc[maturity_mask(rows, start, cut)].copy()
        raw = original(f"{symbol}_funding_events.parquet")
        raw = raw.loc[
            (raw.date >= start - pd.Timedelta(days=1)) & (raw.date <= cut + pd.Timedelta(days=1))
        ]
        if raw.date.duplicated().any() or not raw.date.is_monotonic_increasing:
            raise ValueError("Raw funding clock changed")
        if raw.date.diff().dropna().max() > pd.Timedelta(hours=9):
            raise ValueError("Funding gap exceeds frozen source coverage limit")
        mark_name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-1h-mark.feather"
        mark = original(mark_name).set_index("date").open
        event_marks = (
            mark.reindex(pd.DatetimeIndex(raw.date.dt.floor("h"))).to_numpy()
            * raw.funding_rate.to_numpy()
        )
        for sleeve in ["small", "core"]:
            subset = fit.loc[fit.opportunity_sleeve.eq(sleeve)].copy()
            if subset.empty:
                continue
            labels = horizon_labels(
                candles,
                subset,
                raw.calc_time.to_numpy(),
                event_marks,
                protocol["forecast_bars"][sleeve],
                protocol["fee_per_side"],
            )
            if not labels.label_known_at_ms.lt(cut.value // 1_000_000).all():
                raise ValueError("Training label crosses chronological cutoff")
            all_training.append(pd.concat([subset, labels], axis=1))
    inputs = pd.concat(all_features, ignore_index=True)
    training = pd.concat(all_training, ignore_index=True)
    if inputs.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate common opportunity")
    output.mkdir(parents=True)
    save(output / "protocol.json", protocol)
    inputs.to_parquet(output / "all_opportunity_features.parquet", index=False)
    keep = [
        "symbol",
        "date",
        "feature_open",
        "opportunity_side",
        "opportunity_sleeve",
        *FEATURES,
        "unit_net_horizon_label",
        "horizon_positive_label",
        "entry_at_ms",
        "label_known_at_ms",
        "label_entry_price",
        "label_exit_price",
    ]
    training[keep].to_parquet(output / "training_units.parquet", index=False)
    test = inputs.loc[(inputs.date >= cut) & (inputs.date < end)].copy()
    test["model_ready"] = False
    test["predicted_net_horizon_return"] = np.nan
    test["predicted_horizon_positive_probability"] = np.nan
    models = {}
    for sleeve in ["small", "core"]:
        for side in [1, -1]:
            key = f"{sleeve}_{'long' if side == 1 else 'short'}"
            fit = training.loc[
                training.opportunity_sleeve.eq(sleeve) & training.opportunity_side.eq(side)
            ]
            if len(fit) < protocol["minimum_fit_rows"] or fit.horizon_positive_label.nunique() != 2:
                models[key] = {"status": "INSUFFICIENT_TRAINING", "fit_rows": len(fit)}
                continue
            model = fit_model(
                fit[FEATURES].to_numpy(),
                fit.unit_net_horizon_label.to_numpy(),
                fit.horizon_positive_label.to_numpy(),
                penalty=protocol["penalty"],
            )
            model.update(
                status="FITTED",
                features=FEATURES,
                cutoff_utc=cut.isoformat(),
                last_fit_decision_utc=fit.date.max().isoformat(),
                latest_label_known_at_ms=int(fit.label_known_at_ms.max()),
                latest_fixed_maturity_utc=(fit.date.max() + pd.Timedelta(minutes=1445)).isoformat(),
            )
            models[key] = model
            mask = (
                test.opportunity_sleeve.eq(sleeve)
                & test.opportunity_side.eq(side)
                & test.finite_features
            )
            x = test.loc[mask, FEATURES].to_numpy()
            scaled = (x - np.asarray(model["mean"])) / np.asarray(model["deviation"])
            eligible = (
                np.abs(scaled).max(axis=1) <= protocol["maximum_absolute_standardized_feature"]
            )
            indices = test.loc[mask].index[eligible]
            if len(indices):
                probability, net = predict(model, test.loc[indices, FEATURES].to_numpy())
                test.loc[indices, "model_ready"] = True
                test.loc[indices, "predicted_net_horizon_return"] = net
                test.loc[indices, "predicted_horizon_positive_probability"] = probability
    save(output / "models.json", models)
    destination = output / "predictions"
    destination.mkdir()
    fields = [
        "date",
        "opportunity_side",
        "opportunity_sleeve",
        "model_ready",
        "predicted_net_horizon_return",
        "predicted_horizon_positive_probability",
    ]
    for symbol in protocol["symbols"]:
        test.loc[test.symbol.eq(symbol), fields].to_parquet(
            destination / f"{symbol}.parquet", index=False
        )
    test.to_parquet(output / "heldout_feature_predictions.parquet", index=False)
    save(
        output / "receipt.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "protocol_sha256": sha(protocol_path),
            "training_units": len(training),
            "all_opportunity_rows": len(inputs),
            "heldout_opportunities": len(test),
            "heldout_common_ready": int(test.model_ready.sum()),
            "positive_net_predictions": int(
                (test.model_ready & test.predicted_net_horizon_return.gt(0)).sum()
            ),
            "high_probability_positive_net_predictions": int(
                (
                    test.model_ready
                    & test.predicted_net_horizon_return.gt(0)
                    & test.predicted_horizon_positive_probability.ge(0.8)
                ).sum()
            ),
            "models_sha256": sha(output / "models.json"),
            "prediction_hashes": {
                s: sha(destination / f"{s}.parquet") for s in protocol["symbols"]
            },
            "heldout_outcome_labels_computed": False,
            "future_year_outcomes_computed": False,
            "labels_are_account_profit": False,
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(f.relative_to(output)): sha(f) for f in sorted(output.rglob("*")) if f.is_file()},
    )
    receipt = read(output / "receipt.json")
    print(
        {
            k: receipt[k]
            for k in [
                "training_units",
                "all_opportunity_rows",
                "heldout_common_ready",
                "positive_net_predictions",
                "high_probability_positive_net_predictions",
                "heldout_outcome_labels_computed",
            ]
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.protocol, args.output)
