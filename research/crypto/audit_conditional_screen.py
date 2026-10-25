"""Independent Decimal replay of sampled labels and frozen prediction arithmetic."""

import argparse
import math
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


def replay(frame, offset, sign, spec, events, marks, fee):
    """Scalar path reconstruction, separate from the vectorized label generator."""
    entry_index = offset + 2
    with localcontext() as context:
        context.prec = 40
        entry = Decimal(str(frame.open.iloc[entry_index]))
        take = entry * (1 + sign * Decimal(str(spec["take_price_fraction"])))
        stop = entry * (1 - sign * Decimal(str(spec["stop_price_fraction"])))
        end = entry_index + spec["maximum_bars"]
        exit_price = Decimal(str(frame.open.iloc[end]))
        known_at = frame.date.iloc[end].value // 1_000_000
        for index in range(entry_index, end):
            row = frame.iloc[index]
            high, low, opening = [Decimal(str(row[k])) for k in ["high", "low", "open"]]
            stopped = low <= stop if sign == 1 else high >= stop
            hit = high >= take if sign == 1 else low <= take
            if stopped or hit:
                exit_price = (
                    (min(stop, opening) if sign == 1 else max(stop, opening)) if stopped else take
                )
                known_at = row.date.value // 1_000_000 + 300_000
                break
        entry_at = frame.date.iloc[entry_index].value // 1_000_000
        funding = sum(
            (
                Decimal(str(mark))
                for event, mark in zip(events, marks, strict=True)
                if entry_at <= event < known_at
            ),
            Decimal(0),
        )
        net = sign * (exit_price / entry - 1) - Decimal(str(fee)) * (1 + exit_price / entry)
        net -= sign * funding / entry
        return float(net), int(known_at)


def audit(panel_path, run_path, output):  # noqa: C901
    if output.exists():
        raise ValueError("Preserve audit evidence")
    for directory in [panel_path, run_path]:
        for filename, expected in read(directory / "receipts.json").items():
            if sha(directory / filename) != expected:
                raise ValueError(f"Receipted artifact changed: {filename}")
    plan = read(run_path / "plan.json")
    base = Path(plan["base_dataset"])
    receipts = read(base / "receipts.json")
    if sha(base / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Original source changed")
    rng = np.random.default_rng(20261024)
    errors, prediction_errors = [], []
    for symbol in plan["symbols"]:
        filename = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        frame = pd.read_feather(base / filename)
        frame = frame.loc[
            frame.date.ge(pd.Timestamp(plan["feature_start"], tz="UTC"))
            & frame.date.lt(pd.Timestamp(plan["selection_end"], tz="UTC"))
        ].reset_index(drop=True)
        funding_path = base / f"{symbol}_funding_events.parquet"
        mark_name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-1h-mark.feather"
        for name in [filename, funding_path.name, mark_name]:
            if sha(base / name) != receipts[name]:
                raise ValueError("Audit source changed")
        raw = pd.read_parquet(funding_path)
        mark = pd.read_feather(base / mark_name).set_index("date").open
        marks = (
            mark.reindex(
                pd.DatetimeIndex(pd.to_datetime(raw.calc_time, unit="ms", utc=True).dt.floor("h"))
            ).to_numpy()
            * raw.funding_rate.to_numpy()
        )
        events = raw.calc_time.to_numpy()
        for sleeve in ["small", "core"]:
            spec = plan["labels"][sleeve]
            candidates = np.flatnonzero(
                frame.date.ge(pd.Timestamp(plan["fit_start"], tz="UTC"))
                & (np.arange(len(frame)) + 2 + spec["maximum_bars"] < len(frame))
            )
            chosen = rng.choice(candidates, 40, replace=False)
            for side, sign in [("long", 1), ("short", -1)]:
                labels = pd.read_parquet(
                    panel_path / "labels" / f"{symbol}_{sleeve}_{side}.parquet"
                )
                if len(frame) != len(labels) or not pd.DatetimeIndex(frame.date).equals(
                    pd.DatetimeIndex(labels.date)
                ):
                    raise ValueError("Independent audit clock mismatch")
                for index in chosen:
                    net, clock = replay(frame, int(index), sign, spec, events, marks, 0.0008)
                    if (
                        clock != labels.label_known_at_ms.iloc[index]
                        or int(net > 0) != labels.net_win_label.iloc[index]
                    ):
                        raise ValueError("Independent clock or net-win mismatch")
                    errors.append(abs(net - labels.net_return_label.iloc[index]))
    models = read(run_path / "models.json")
    for key, model in models.items():
        group, sleeve, side = key.rsplit("_", 2)
        predictions = pd.read_parquet(run_path / "predictions" / f"{group}_{sleeve}.parquet")
        sample = predictions.loc[predictions.side.eq(side)].sample(40, random_state=20261024)
        for symbol, subset in sample.groupby("symbol"):
            inputs = pd.read_parquet(panel_path / f"{symbol}.parquet").set_index("date")
            for row in subset.itertuples():
                values = inputs.loc[row.date, model["features"]]
                standardized = [
                    (float(v) - m) / s
                    for v, m, s in zip(values, model["mean"], model["deviation"], strict=True)
                ]
                score = model["logistic"][0] + sum(
                    x * w for x, w in zip(standardized, model["logistic"][1:], strict=True)
                )
                probability = 1 / (1 + math.exp(-score))
                net = model["linear"][0] + sum(
                    x * w for x, w in zip(standardized, model["linear"][1:], strict=True)
                )
                prediction_errors.extend(
                    [
                        abs(probability - row.predicted_win_probability),
                        abs(net - row.predicted_net_return),
                    ]
                )
    if max(errors) > 1e-10 or max(prediction_errors) > 1e-10:
        raise ValueError("Independent arithmetic mismatch")
    save(
        output,
        {
            "passed": True,
            "labels_audited": len(errors),
            "predictions_audited": len(prediction_errors) // 2,
            "maximum_decimal_label_error": float(max(errors)),
            "maximum_prediction_error": float(max(prediction_errors)),
            "panel_receipts_sha256": sha(panel_path / "receipts.json"),
            "screen_receipts_sha256": sha(run_path / "receipts.json"),
            "audit_source_sha256": sha(Path(__file__)),
            "reserved_test_labels_read": False,
            "limits": (
                "Audits registered OHLC and hourly mark proxies; "
                "not exchange fill or production execution evidence"
            ),
            "production_eligible": False,
        },
    )
    print(read(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.panel.absolute(), args.run.absolute(), args.output.absolute())
