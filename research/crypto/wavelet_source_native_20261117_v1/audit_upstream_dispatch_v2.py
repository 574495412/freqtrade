"""Correct source ownership, including the subclass's end-of-file jump override."""

# ruff: noqa: S101, S102
# Source-only AST execution with an explicit numerical-path boundary.
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Source audit requires assertions")


def methods(path, name, selected, namespace):
    cls = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == name
    )
    definitions = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in selected]
    assert len(definitions) == len(selected)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
    return {key: namespace[key] for key in selected}


def main():
    folder = HERE / "sources/nateemma_strategies/TSPredict"
    namespace = {"np": np, "DataFrame": pd.DataFrame, "RobustScaler": lambda: SimpleNamespace()}
    base = methods(folder / "TSPredict.py", "TSPredict", {"add_predictions"}, namespace)
    child = methods(
        folder / "TS_Wavelet.py", "TS_Wavelet", {"add_jumping_predictions", "init_model"}, namespace
    )
    parent_type = type("OriginalTSPredictDispatcher", (), base)
    child_type = type("OriginalTSWaveletDispatcher", (parent_type,), child)
    owner = child_type()
    owner.static_model, owner.use_rolling, owner.training_mode = False, False, False
    owner.lookahead = 6
    owner.custom_trade_info = {}
    owner.curr_pair = "SOURCE_DISPATCH_DIAGNOSTIC"
    calls = []

    def numerical_boundary(frame):
        calls.append({"path": "TS_Wavelet.add_rolling_predictions", "rows": len(frame)})
        result = frame.copy()
        result["predicted_gain"] = 0.0
        return result

    owner.add_rolling_predictions = numerical_boundary
    frame = pd.DataFrame({"close": np.full(300, 100.0), "gain": np.zeros(300)})
    result = owner.add_predictions(frame)
    assert calls == [{"path": "TS_Wavelet.add_rolling_predictions", "rows": 300}]
    assert result.predicted_gain.eq(0).all()
    source_class = next(
        n
        for n in ast.parse((folder / "TS_Wavelet_DWT.py").read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "TS_Wavelet_DWT"
    )
    assert not any(
        isinstance(n, ast.FunctionDef) and n.name in ["add_predictions", "add_jumping_predictions"]
        for n in source_class.body
    )
    record = {
        "status": "CURRENT_SOURCE_DEFAULT_ROUTES_TO_SUBCLASS_ROLLING",
        "passed_source_dispatch_probe": True,
        "commit": "9bb7dab5df53203e69f60336749551a40767af0b",
        "original_methods_executed": [
            "TSPredict.add_predictions",
            "TS_Wavelet.init_model",
            "TS_Wavelet.add_jumping_predictions",
        ],
        "explicit_numerical_boundary": (
            "add_rolling_predictions replaced by call recorder; full numerical body not executed"
        ),
        "route_calls": calls,
        "default_use_rolling_false_still_redirects_to_rolling": True,
        "previous_default_signature_mismatch_claim_withdrawn": True,
        "model_or_forecast_validity_certified": False,
        "native_constructor_executed": False,
        "new_financial_runs": 0,
        "new_native_wallets": 0,
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                folder / "TSPredict.py",
                folder / "TS_Wavelet.py",
                folder / "TS_Wavelet_DWT.py",
            ]
        },
    }
    (HERE / "upstream_dispatch_audit_v2.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
