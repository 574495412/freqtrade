"""Execute source control-flow with recording stand-ins, never XGBoost or an account.

Wavelet/scaler/model stand-ins expose row ownership, fit/assignment chronology and
array aliasing. Their outputs are diagnostic watermarks, not forecasts or returns.
"""

# ruff: noqa: S101, S102
# This audit requires assertions and evaluates selected pinned source definitions only.
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE / "sources/rmallarapu-bc_nateemma/binanceus/DWT_Predict.py"
if not __debug__:
    raise RuntimeError("Chronology audit requires assertions")


class RecordingWavelet:
    def __init__(self):
        self.calls = []

    def wavedec(self, values, wavelet, mode, level):
        self.calls.append(
            {
                "input_rows": len(values),
                "first": float(values[0]),
                "last": float(values[-1]),
                "wavelet": wavelet,
                "mode": mode,
                "level": level,
            }
        )
        return [np.array(values)]


class RecordingScaler:
    def __init__(self):
        self.fits = []

    def fit(self, frame):
        self.fits.append(
            {
                "rows": len(frame),
                "first_id": int(frame.iloc[0]["_row_id"]),
                "last_id": int(frame.iloc[-1]["_row_id"]),
            }
        )
        return self

    def transform(self, frame):
        return frame.to_numpy()


class RecordingRegressor:
    def __init__(self, original_closes):
        self.original = np.array(original_closes)
        self.fits = []
        self.predictions = []

    def fit(self, x, y):
        ids = x[:, 0].astype(int)
        label_ids = ids + 6
        assert len(x) == len(y) and (label_ids < len(self.original)).all()
        changed = np.flatnonzero(y != self.original[label_ids])
        self.fits.append(
            {
                "first_feature_id": int(ids[0]),
                "last_feature_id": int(ids[-1]),
                "latest_label_id": int(label_ids[-1]),
                "training_rows": len(x),
                "labels_changed_from_original_closes": len(changed),
            }
        )
        return self

    def predict(self, x):
        ids = x[:, 0].astype(int)
        latest = self.fits[-1]["latest_label_id"]
        self.predictions.append(
            {
                "first_output_id": int(ids[0]),
                "last_output_id": int(ids[-1]),
                "fit_call": len(self.fits) - 1,
                "latest_training_label_id": latest,
                "output_rows": len(ids),
            }
        )
        # Watermark lets the audit detect historical assignment and subsequent label reuse.
        # It is deliberately unrelated to any prediction algorithm or economic signal.
        return 1e12 + latest * 1e6 + ids.astype(float)


def source_methods():
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DWT_Predict")
    names = {
        "get_coeffs",
        "get_coefficient_table",
        "merge_coeff_table",
        "merge_data",
        "convert_dataframe",
        "train_model",
        "get_predictions",
        "add_jumping_predictions",
        "add_predictions",
    }
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    recorder = RecordingWavelet()
    ns = {"np": np, "pd": pd, "DataFrame": pd.DataFrame, "pywt": recorder}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(SOURCE), "exec"), ns)
    return methods, ns, recorder


def probe(frame):
    _, ns, wavelet = source_methods()
    attrs = {
        name: ns[name]
        for name in [
            "get_coeffs",
            "get_coefficient_table",
            "merge_coeff_table",
            "merge_data",
            "convert_dataframe",
            "train_model",
            "get_predictions",
            "add_jumping_predictions",
            "add_predictions",
        ]
    }
    probe_type = type("SourceChronologyProbe", (), attrs)
    owner = probe_type()
    owner.model_window, owner.lookahead = 128, 6
    owner.scaler = RecordingScaler()
    owner.coeff_model = RecordingRegressor(frame.close.to_numpy())
    owner.dp = SimpleNamespace(runmode=SimpleNamespace(value="backtest"))
    original = frame.close.copy()
    result = owner.add_predictions(frame.copy())
    pd.testing.assert_series_equal(frame.close, original)
    fits = owner.coeff_model.fits
    assignments = owner.coeff_model.predictions
    last_label = np.full(len(frame), -1, dtype=int)
    last_fit = np.full(len(frame), -1, dtype=int)
    for row in assignments:
        region = slice(row["first_output_id"], row["last_output_id"] + 1)
        last_label[region] = row["latest_training_label_id"]
        last_fit[region] = row["fit_call"]
    ids = np.arange(len(frame))
    assigned = last_fit >= 0
    future = assigned & (last_label > ids)
    admitted = future & (ids >= 128)
    assert future.any() and admitted.any()
    assert owner.scaler.fits == [{"rows": len(frame), "first_id": 0, "last_id": len(frame) - 1}]
    assert wavelet.calls[0]["input_rows"] == 126
    assert wavelet.calls[0]["first"] == float(original.iloc[1])
    assert wavelet.calls[0]["last"] == float(original.iloc[126])
    raw = result.model_predict.to_numpy()
    assert (raw[assigned] > 1e12).all()
    return (
        {
            "rows": len(frame),
            "fits": fits,
            "assignments": assignments,
            "normalization_fits": owner.scaler.fits,
            "assigned_rows": int(assigned.sum()),
            "rows_with_future_training_labels": int(future.sum()),
            "rows_with_future_training_labels_after128warmup": int(admitted.sum()),
            "maximum_training_label_lead_bars": int((last_label[assigned] - ids[assigned]).max()),
            "first_wavelet_call": wavelet.calls[0],
            "prediction_values_are_only_watermarks": True,
        },
        last_label,
        raw,
    )


def main():
    for row in json.loads((HERE / "source_receipts.json").read_text()):
        raw = Path(row["local_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
    # These are existing real prices, not source training data or financial-account evidence.
    plan = json.loads(
        (HERE.with_name("trendcore_source_native_20261117_v1") / "plan.json").read_text()
    )
    path = Path(plan["datadir"]) / "BTC_USDT-5m.feather"
    data = (
        pd.read_feather(path)
        .loc[lambda x: x.date.ge(pd.Timestamp("2025-01-01T00:00Z"))]
        .head(3000)
        .reset_index(drop=True)
    )
    assert len(data) == 3000 and data.date.diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    frame = pd.DataFrame({"_row_id": np.arange(len(data)), "date": data.date, "close": data.close})
    full, labels, watermarks = probe(frame)
    prefix, prefix_labels, prefix_watermarks = probe(frame.iloc[:2800])
    common = np.arange(2800) >= 128
    assignment_changes = int(((labels[:2800] != prefix_labels) & common).sum())
    watermark_changes = int(((watermarks[:2800] != prefix_watermarks) & common).sum())
    assert assignment_changes > 0 and watermark_changes > 0
    assert any(row["labels_changed_from_original_closes"] > 0 for row in full["fits"][1:])
    result = {
        "status": "LEGACY_SOURCE_CHRONOLOGY_REJECTED",
        "passed_diagnostic": True,
        "kind": (
            "Source-AST row-clock audit with stand-ins, "
            "not model numerical replication or native backtest"
        ),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "data_receipt": {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "subset_start": str(data.date.iloc[0]),
            "subset_last": str(data.date.iloc[-1]),
            "rows": 3000,
        },
        "stand_ins": [
            "wavelet returns input solely to trace source slices",
            "scaler is identity solely to preserve row IDs",
            "regressor emits watermarks solely to expose assignment and aliasing",
        ],
        "full": full,
        "prefix": prefix,
        "appended200rows_change_past_training_assignment_afterwarmup": assignment_changes,
        "appended200rows_change_past_watermarks_afterwarmup": watermark_changes,
        "source_xgboost_executed": False,
        "real_pywavelets_transform_executed": False,
        "source_financial_engine_executed": False,
        "new_native_wallets": 0,
        "profit_proven": False,
        "not_a_claim_about_current_upstream": True,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "chronology_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "full": full,
                "changed_prior_assignment_rows": assignment_changes,
                "new_native_wallets": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
