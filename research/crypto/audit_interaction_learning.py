"""Raw Decimal unit replay, chronological population checks and scalar forecast review."""

import argparse
import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.interaction_features import FEATURES, read_market
from research.crypto.interaction_models import enable_runtime, scalar_predict, verify_sources


def audit_units(plan, units, sample_count):
    receipts, cache, errors = read(Path(plan["dataset"]) / "receipts.json"), {}, {}

    def market(symbol, kind):
        key = (symbol, kind)
        if key not in cache:
            cache[key] = read_market(plan["dataset"], receipts, symbol, kind).set_index("date")
        return cache[key]

    def equal(field, actual, expected, tolerance=1e-10):
        error = abs(float(actual) - float(expected))
        errors[field] = max(errors.get(field, 0), error)
        if not math.isclose(float(actual), float(expected), rel_tol=1e-8, abs_tol=tolerance):
            raise ValueError((field, actual, expected))

    chosen = units.iloc[np.linspace(0, len(units) - 1, min(sample_count, len(units)), dtype=int)]
    for number, row in enumerate(chosen.itertuples(index=False)):
        prices = market(row.symbol, "5m-futures")
        source = pd.Timestamp(row.feature_open)
        begin, finish = (
            source + pd.Timedelta(minutes=10),
            source + pd.Timedelta(minutes=10, hours=row.horizon_hours),
        )
        if row.entry != begin or row.label_known_at != finish:
            raise ValueError("Label endpoints do not match the frozen decision clock")
        first, last = (
            Decimal(str(prices.loc[begin, "open"])),
            Decimal(str(prices.loc[finish, "open"])),
        )
        q, side = 1 / first, Decimal(int(row.side))
        funding, mark = market(row.symbol, "1h-funding_rate"), market(row.symbol, "1h-mark")
        events = funding.loc[(funding.index >= begin) & (funding.index <= finish)]
        cash = Decimal(0)
        for clock, event in events.iterrows():
            index = mark.index.get_indexer([clock], method="pad", tolerance=pd.Timedelta(hours=1))[
                0
            ]
            if index < 0:
                raise ValueError("Funding mark missing")
            cash -= (
                side * q * Decimal(str(event.funding_rate)) * Decimal(str(mark.iloc[index].open))
            )
        target = side * q * (last - first) + cash - q * (first + last) * Decimal(".0008")
        equal("net_label", row.net_label_3bps, target, 1e-12)
        equal("funding_credit", row.funding_credit, cash, 1e-12)
        position = prices.index.get_loc(source)
        close = float(prices.close.iloc[position])
        for bars in [1, 12, 48, 288]:
            expected = row.side * math.log(close / prices.close.iloc[position - bars])
            equal(f"signed_r{bars}", getattr(row, f"signed_r{bars}"), expected)
        previous = prices.close.iloc[position - 14 : position].to_numpy()
        candles = prices.iloc[position - 13 : position + 1]
        atr = (
            math.fsum(
                max(high - low, abs(high - prior), abs(low - prior))
                for high, low, prior in zip(candles.high, candles.low, previous, strict=True)
            )
            / 14
        )
        equal("atr", row.atr, atr)
        known = funding.loc[funding.index + pd.Timedelta(hours=1) <= row.date]
        hours = (known.index[-1] - known.index[-2]).total_seconds() / 3600
        equal(
            "known_funding_credit8",
            row.funding_credit8,
            -row.side * known.funding_rate.iloc[-1] * 8 / hours,
        )
        if number < 3:
            returns = []
            for symbol in plan["symbols"]:
                price = market(symbol, "5m-futures")
                returns.append(
                    math.log(
                        price.loc[source, "close"]
                        / price.loc[source - pd.Timedelta(hours=1), "close"]
                    )
                )
            equal(
                "cross_section_relative12",
                row.signed_relative12,
                row.side
                * (math.log(close / prices.close.iloc[position - 12]) - float(np.median(returns))),
            )
    return {"units_checked": len(chosen), "maximum_absolute_errors": errors}


def audit(plan_path, panel, learning=None, evaluation=None):  # noqa: C901 - independent phase checks
    plan = read(plan_path)
    verify_sources(plan)
    prepared = read(panel / "receipt.json")
    if prepared["plan_sha256"] != sha(plan_path):
        raise ValueError("Prepared protocol changed")
    for name, digest in prepared["file_hashes"].items():
        if sha(panel / name) != digest:
            raise ValueError("Prepared data changed")
    identity = source_identity()
    if read(panel / "freeze.json")["source_identity"] != identity:
        raise ValueError("Native framework changed")
    h1 = pd.read_parquet(panel / "h1_units.parquet")
    result = {
        "H1_raw_units": audit_units(plan, h1, 120),
        "passed": True,
        "native_source_files_unchanged": len(identity),
        "orders_enabled": False,
        "production_eligible": False,
        "plan_sha256": sha(plan_path),
        "audit_source_sha256": sha(Path(__file__)),
    }
    if learning is not None:
        enable_runtime(plan)
        import joblib

        receipt = read(learning / "receipt.json")
        if receipt["plan_sha256"] != sha(plan_path):
            raise ValueError("Learning protocol mismatch")
        features = pd.read_parquet(panel / "all_features.parquet").set_index("row_id")
        predictions = pd.read_parquet(learning / "all_predictions.parquet")
        if sha(learning / "all_predictions.parquet") != receipt["predictions_sha256"]:
            raise ValueError("Forecasts changed")
        errors, populations = [], []
        for meta in receipt["model_metadata"]:
            key = meta["key"]
            path = learning / "models" / f"{key}.joblib"
            population = learning / "models" / f"{key}_population.parquet"
            if sha(path) != meta["model_sha256"] or sha(population) != meta["population_sha256"]:
                raise ValueError("Model or admitted population changed")
            actual = pd.read_parquet(population)
            if actual.row_id.duplicated().any():
                raise ValueError("Overlapping fit/calibration units")
            for role in ["train", "calibration"]:
                prefix = "training" if role == "train" else "calibration"
                lower, upper = (
                    pd.Timestamp(meta[f"{prefix}_start"]),
                    pd.Timestamp(meta[f"{prefix}_end"]),
                )
                expected = features.loc[
                    features.sleeve.eq(meta["sleeve"])
                    & features.date.ge(lower)
                    & (features.date + pd.Timedelta(days=1, minutes=5)).lt(upper)
                ].index
                selected = actual.loc[actual.role.eq(role)]
                if (
                    set(selected.row_id) != set(expected)
                    or not selected.label_known_at.lt(upper).all()
                ):
                    raise ValueError("Chronological model population incorrect")
                if upper >= pd.Timestamp(meta["forecast_start"]):
                    raise ValueError("Calibration overlaps forecast month")
            bundle = joblib.load(path)
            samples = predictions.loc[predictions.model_key.eq(key)].drop_duplicates("row_id")
            samples = samples.iloc[
                np.linspace(0, len(samples) - 1, min(6, len(samples)), dtype=int)
            ]
            for row in samples.itertuples(index=False):
                raw, probability, net = scalar_predict(
                    bundle, features.loc[row.row_id, FEATURES].to_numpy(dtype=float)
                )
                error = max(
                    abs(raw - row.raw_probability),
                    abs(probability - row.probability),
                    abs(net - row.predicted_net),
                )
                if error > 1e-10:
                    raise ValueError("Independent scalar forecast mismatch")
                errors.append(error)
            populations.append(
                {"key": key, "population_rows": len(actual), "forecast_samples": len(samples)}
            )
        result.update(
            model_populations_checked=populations,
            independent_prediction_samples=len(errors),
            maximum_scalar_prediction_error=max(errors),
            prediction_hashes_unchanged=True,
        )
    if evaluation is not None:
        labels = pd.read_parquet(evaluation / "validation_units.parquet")
        full = pd.read_parquet(panel / "validation_features.parquet").merge(
            labels.drop(
                columns=["symbol", "date", "feature_open", "side", "sleeve", "horizon_hours"]
            ),
            on="row_id",
            validate="one_to_one",
        )
        result["H2_raw_units"] = audit_units(plan, full, 120)
        record = read(evaluation / "result.json")
        if len(record["unit_results"]) != 18:
            raise ValueError("Changed comparison family")
        ranked = sorted(
            enumerate(record["unit_results"]), key=lambda x: x[1]["one_sided_block_pvalue"]
        )
        ceiling = 1.0
        for rank in reversed(range(18)):
            _, row = ranked[rank]
            ceiling = min(ceiling, row["one_sided_block_pvalue"] * 18 / (rank + 1))
            if abs(ceiling - row["unified_BH_qvalue"]) > 1e-12:
                raise ValueError("Unified comparison adjustment differs")
        result["unified_comparisons_checked"] = 18
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--learning", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve prior audit")
    result = audit(args.plan, args.panel, args.learning, args.evaluation)
    save(args.output, result)
    print(
        {
            "passed": result["passed"],
            "H1_units": result["H1_raw_units"]["units_checked"],
            "forecast_samples": result.get("independent_prediction_samples"),
            "H2_units": result.get("H2_raw_units", {}).get("units_checked"),
        }
    )
