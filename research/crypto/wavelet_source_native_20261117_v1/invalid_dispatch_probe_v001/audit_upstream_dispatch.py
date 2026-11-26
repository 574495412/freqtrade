"""Source-only probe of the current default dispatch, not native initialization."""

# ruff: noqa: S101, S102
# Selected original AST methods and assertions; no external module imports or models.
import ast
import copy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Source dispatch audit requires assertions")


def methods(path, class_name, names, namespace):
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return {name: namespace[name] for name in names}


def main():
    folder = HERE / "sources/nateemma_strategies/TSPredict"
    base = folder / "TSPredict.py"
    wavelet = folder / "TS_Wavelet.py"
    namespace = {
        "np": np,
        "copy": copy,
        "DataFrame": pd.DataFrame,
        "RobustScaler": lambda: SimpleNamespace(kind="diagnostic-no-scaling"),
    }
    base_methods = methods(
        base, "TSPredict", {"add_predictions", "add_jumping_predictions"}, namespace
    )
    wavelet_methods = methods(
        wavelet, "TS_Wavelet", {"init_model", "train_model", "predict_data"}, namespace
    )
    base_type = type("PinnedTSPredictDispatch", (), base_methods)
    child_type = type("PinnedTSWaveletDispatch", (base_type,), wavelet_methods)
    owner = child_type()
    owner.static_model = False
    owner.use_rolling = False
    owner.training_mode = False
    owner.supports_incremental_training = True
    owner.lookahead = 6
    owner.train_len = 128
    owner.model_window = 64
    owner.scale_len = 8
    owner.curr_pair = "SOURCE_DISPATCH_DIAGNOSTIC"
    owner.custom_trade_info = {}
    owner.forecaster = SimpleNamespace(kind="no-model-loaded")
    owner.get_future_gain = lambda frame: np.zeros(len(frame))
    owner.get_data = lambda frame: (np.arange(len(frame), dtype=float).reshape(-1, 1), None)
    frame = pd.DataFrame({"close": np.full(300, 100.0), "gain": np.zeros(300)})
    try:
        owner.add_predictions(frame)
    except TypeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected published caller/callee arity mismatch")
    assert "predict_data" in message and "positional" in message
    # Independent signature binding confirms the callee accepts one bound argument.
    signature = inspect.signature(owner.predict_data)
    try:
        signature.bind(owner.forecaster, np.zeros((1, 1)))
    except TypeError:
        bind_rejected = True
    else:
        bind_rejected = False
    assert bind_rejected
    result = {
        "status": "CURRENT_SOURCE_DEFAULT_DISPATCH_SIGNATURE_MISMATCH",
        "commit": "9bb7dab5df53203e69f60336749551a40767af0b",
        "selected_source_defaults": {
            "static_model": False,
            "use_rolling": False,
            "training_mode": False,
            "lookahead": 6,
            "train_len": 128,
            "model_window": 64,
        },
        "original_base_dispatch_and_original_subclass_methods_executed": True,
        "helper_stand_ins": [
            "300 synthetic constant rows",
            "get_data row IDs only",
            "get_future_gain zero labels only",
            "RobustScaler no-op constructor",
            "forecaster only a namespace, never forecast",
        ],
        "caller": "TSPredict.add_jumping_predictions -> self.predict_data(pair_forecaster, dslice)",
        "callee": "TS_Wavelet.predict_data(self, X)",
        "exception": message,
        "independent_signature_binding_rejected": True,
        "numerical_model_executed": False,
        "native_constructor_executed": False,
        "full_framework_or_alternative_configuration_certified": False,
        "did_not_switch_to_rolling": True,
        "financial_runs": 0,
        "new_native_wallets": 0,
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [base, wavelet, folder / "TS_Wavelet_DWT.py"]
        },
    }
    (HERE / "upstream_dispatch_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
