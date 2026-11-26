"""Current source CPU rolling path clock audit with explicit numerical stand-ins."""

# ruff: noqa: S101, S102
# Selected original methods execute only with diagnostic row-ID features and model stand-ins.
import ast
import hashlib
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Source clock audit requires assertions")


class RowClockWavelet:
    def set_lookahead(self, n):
        assert n == 6

    def get_coeffs(self, window):
        assert len(window) == 64
        assert np.array_equal(window, np.arange(window[0], window[-1] + 1))
        return np.array([window[-1]])

    def coeff_to_array(self, values):
        return values

    def array_to_coeff(self, values):
        return values

    def get_values(self, values):
        return values


class IdentityScaler:
    def fit_transform(self, values):
        return values.copy()

    def transform(self, values):
        return values.copy()


def extracted(path, class_name, names, namespace):
    cls = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == class_name
    )
    definitions = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(definitions) == len(names)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    return {name: namespace[name] for name in names}


def probe(n):
    fits = []
    predictions = []
    scales = []

    class RecordingModel:
        def __init__(self, unused):
            pass

        def fit(self, x, y):
            fits.append(
                {
                    "training_rows": len(x),
                    "latest_feature_source_clock": int(x[-1, 0]),
                    "latest_label_source_clock": int(y[-1, 0]),
                }
            )

        def predict(self, x):
            row = {
                "first_output_clock": int(x[0, 0]),
                "last_output_clock": int(x[-1, 0]),
                "latest_label_source_clock": fits[-1]["latest_label_source_clock"],
                "rows": len(x),
            }
            assert row["latest_label_source_clock"] < row["first_output_clock"]
            predictions.append(row)
            return x.copy()

    folder = HERE / "sources/nateemma_strategies/TSPredict"
    ns = {
        "np": np,
        "DataFrame": pd.DataFrame,
        "traceback": traceback,
        "sliding_window_view": sliding_window_view,
        "RobustScaler": IdentityScaler,
        "MultiOutputRegressor": RecordingModel,
        "LGBMRegressor": lambda **kwargs: SimpleNamespace(),
        "scaler_exists": lambda *args: False,
        "Wavelets": SimpleNamespace(WaveletType=SimpleNamespace(SWT="SWT")),
    }
    base = extracted(folder / "TSPredict.py", "TSPredict", {"add_predictions"}, ns)
    child = extracted(
        folder / "TS_Wavelet.py",
        "TS_Wavelet",
        {
            "init_model",
            "build_coefficient_table",
            "merge_coeff_table",
            "add_jumping_predictions",
            "add_rolling_predictions",
        },
        ns,
    )
    parent_type = type("OriginalBaseDispatcher", (), base)
    child_type = type("OriginalWaveletRollingCPU", (parent_type,), child)
    owner = child_type()
    owner.static_model, owner.use_rolling, owner.training_mode = False, False, False
    owner.custom_trade_info = {}
    owner.curr_pair = "ROW_CLOCK_ONLY"
    owner.lookahead = 6
    owner.train_min_len = 64
    owner.wavelet_size = 64
    owner.wavelet_type = "DWT"
    owner.wavelet = RowClockWavelet()
    owner.forecaster = SimpleNamespace(
        requires_pretraining=lambda: True, supports_multiple_columns=lambda: True
    )
    owner.single_col_prediction = True
    owner.merge_indicators = False
    owner.use_mlx = False
    owner.scale_results = True
    owner.get_storage_location = lambda: "DIAGNOSTIC_NO_SCALER_IO"

    def set_data(frame):
        owner.data = np.arange(len(frame), dtype=float)

    def scale_array(window, values):
        predicted_clock = int(values[0])
        assert window[-1] == predicted_clock - 1
        scales.append((predicted_clock, int(window[-1])))
        return values

    owner.set_data = set_data
    owner.scale_array = scale_array
    frame = pd.DataFrame(
        {"close": np.full(n, 100.0), "gain": np.zeros(n), "predicted_gain": np.zeros(n)}
    )
    result = owner.add_predictions(frame)
    assert predictions and len(scales) == n - 1000
    assert result.predicted_gain.iloc[:1000].eq(0).all()
    row_labels = {
        i: r["latest_label_source_clock"]
        for r in predictions
        for i in range(r["first_output_clock"], r["last_output_clock"] + 1)
    }
    assert len(row_labels) == n - 1000
    return {
        "rows": n,
        "fits": fits,
        "prediction_windows": predictions,
        "scaling_windows_checked": len(scales),
        "first_feature_clock": 63,
        "normalized_features_real_model_and_prices_not_executed": True,
    }, row_labels


def main():
    full, labels = probe(6000)
    short, prefix_labels = probe(5800)
    assert all(labels[i] == value for i, value in prefix_labels.items())
    result = {
        "status": "CURRENT_CPU_ROLLING_CONTROL_CLOCKS_PASS_ONLY",
        "full": full,
        "prefix": short,
        "appended200rows_changed_prior_training_model_clock": 0,
        "exact_source_methods_executed": [
            "TSPredict.add_predictions",
            "TS_Wavelet.init_model",
            "TS_Wavelet.add_jumping_predictions",
            "TS_Wavelet.build_coefficient_table",
            "TS_Wavelet.merge_coeff_table",
            "TS_Wavelet.add_rolling_predictions",
        ],
        "selected_branch": (
            "CPU, DWT, no stored scaler, source default sixbar horizon and2500row chunks"
        ),
        "stand_ins": [
            "set_data gives row IDs, not normalized market features",
            "wavelet coefficients/inverse carry row clocks only",
            "scaler identity; no artifact IO",
            "LightGBM wrapper records fit/predict clocks only",
            "scale_array identity while checking the source past32row slices",
        ],
        "real_wavelet_model_scaler_or_native_account_certified": False,
        "actual_feature_pipeline_causality_not_proven": True,
        "external_scaler_provenance_not_proven": True,
        "native_constructor_executed": False,
        "new_native_wallets": 0,
        "new_financial_runs": 0,
        "source_inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                HERE / "sources/nateemma_strategies/TSPredict/TSPredict.py",
                HERE / "sources/nateemma_strategies/TSPredict/TS_Wavelet.py",
            ]
        },
    }
    (HERE / "upstream_clock_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
