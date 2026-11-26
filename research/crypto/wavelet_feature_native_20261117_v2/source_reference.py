"""Run selected pinned source methods with real numerical libraries, not an account.

Imports unrelated NN/GAN frameworks are intentionally not executed. Selected AST
bodies, actual wavelet/scaler/model classes and source flags are unchanged.
"""

# ruff: noqa: S101, S102, S307
# Source-definition execution and checksum assertions are the reference boundary.
import ast
import hashlib
import json
import sys
import traceback
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
OLD = HERE.with_name("wavelet_source_native_20261117_v1")
RF = HERE.with_name("freqai_rf_native_20261114_v1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bootstrap():
    for root, manifest in [
        (RF / "dependencies", RF / "dependency_manifest.json"),
        (HERE / "dependencies", HERE / "dependency_manifest.json"),
    ]:
        for relative, digest in json.loads(manifest.read_text())["files"].items():
            assert sha(root / relative) == digest, relative
    sys.path.insert(0, str(RF / "dependencies"))
    sys.path.insert(0, str(HERE / "dependencies"))
    import numpy as np
    import pandas as pd
    import pywt
    from lightgbm import LGBMRegressor
    from numpy.lib.stride_tricks import sliding_window_view
    from sklearn.linear_model import SGDRegressor
    from sklearn.multioutput import MultiOutputRegressor
    from sklearn.preprocessing import RobustScaler

    return {
        "np": np,
        "pd": pd,
        "pywt": pywt,
        "DataFrame": pd.DataFrame,
        "Series": pd.Series,
        "LGBMRegressor": LGBMRegressor,
        "SGDRegressor": SGDRegressor,
        "MultiOutputRegressor": MultiOutputRegressor,
        "RobustScaler": RobustScaler,
        "sliding_window_view": sliding_window_view,
        "traceback": traceback,
        "ABC": ABC,
        "abstractmethod": abstractmethod,
        "Enum": Enum,
        "auto": auto,
        "dataclass": dataclass,
        "field": field,
        "Any": Any,
        "Path": Path,
    }


def definitions(path, base, select):
    name = "_wavelet_source_reference_" + path.stem
    module = types.ModuleType(name)
    module.__dict__.update(base)
    module.__dict__.update(__name__=name, __file__=str(path))
    sys.modules[name] = module
    selected = [n for n in ast.parse(path.read_text()).body if select(n)]
    assert selected, path
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), module.__dict__)
    return module


def method_definitions(path, class_name, names, namespace):
    cls = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == class_name
    )
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names), (class_name, names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return {name: namespace[name] for name in names}, cls


def literal(cls, name):
    for n in cls.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in n.targets
        ):
            return ast.literal_eval(n.value)
    raise KeyError(name)


def build_reference():
    base = bootstrap()
    gan = definitions(
        HERE / "sources/GANs/GANType.py",
        base,
        lambda n: isinstance(n, ast.ClassDef) and n.name == "GANType",
    )
    cfg = definitions(
        HERE / "sources/Framework/BaseStrategy.py",
        {**base, "GANType": gan.GANType},
        lambda n: (
            isinstance(n, ast.ClassDef)
            and n.name in ["NormalizationType", "ModelType", "StrategyConfig"]
        ),
    )
    detrend = definitions(
        HERE / "sources/utils/Detrenders.py",
        base,
        lambda n: (
            isinstance(n, ast.ClassDef)
            or (isinstance(n, ast.FunctionDef) and n.name == "make_detrender")
        ),
    )
    forecasters = definitions(
        OLD / "sources/nateemma_strategies/utils/Forecasters.py",
        {**base, "Detrenders": detrend},
        lambda n: isinstance(n, ast.ClassDef) and n.name in ["base_forecaster", "sgd_forecaster"],
    )
    wavelets = definitions(
        OLD / "sources/nateemma_strategies/utils/Wavelets.py",
        base,
        lambda n: (
            isinstance(n, ast.ClassDef)
            or (isinstance(n, ast.FunctionDef) and n.name == "make_wavelet")
        ),
    )
    scaler = definitions(
        HERE / "sources/utils/Scalers.py",
        base,
        lambda n: isinstance(n, ast.FunctionDef) and n.name in ["get_scaler_path", "scaler_exists"],
    )
    helper_ns = {
        **base,
        "__name__": "source_normalizer",
        "__file__": str(HERE / "sources/utils/DataframeUtils.py"),
    }
    normalizer, _ = method_definitions(
        HERE / "sources/utils/DataframeUtils.py",
        "DataframeUtils",
        {"normalize_selected_columns", "rolling_robust_scale", "is_strictly_numeric"},
        helper_ns,
    )
    base_ns = {
        **base,
        "__name__": "source_strategy_config",
        "__file__": str(HERE / "sources/Framework/BaseStrategy.py"),
    }
    storage, _ = method_definitions(
        HERE / "sources/Framework/BaseStrategy.py",
        "BaseStrategy",
        {"get_storage_location"},
        base_ns,
    )
    common = {
        **base,
        "Wavelets": wavelets,
        "scaler_exists": scaler.scaler_exists,
        "NormalizationType": cfg.NormalizationType,
        "ModelType": cfg.ModelType,
        "StrategyConfig": cfg.StrategyConfig,
        "__name__": "source_predict",
        "__file__": str(OLD / "sources/nateemma_strategies/TSPredict/TSPredict.py"),
    }
    folder = OLD / "sources/nateemma_strategies/TSPredict"
    parent, parent_node = method_definitions(
        folder / "TSPredict.py", "TSPredict", {"add_predictions", "convert_dataframe"}, common
    )
    child, child_node = method_definitions(
        folder / "TS_Wavelet.py",
        "TS_Wavelet",
        {
            "init_model",
            "set_data",
            "build_coefficient_table",
            "merge_coeff_table",
            "add_jumping_predictions",
            "add_rolling_predictions",
        },
        common,
    )
    parent.update(storage)
    for name in [
        "include_list",
        "pre_normalized_columns",
        "static_model",
        "training_mode",
        "scale_results",
    ]:
        parent[name] = literal(parent_node, name)
    for name in [
        "lookahead",
        "wavelet_size",
        "use_rolling",
        "single_col_prediction",
        "merge_indicators",
    ]:
        child[name] = literal(child_node, name)
    config_node = next(
        n.value
        for n in child_node.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "strategy_config" for t in n.targets)
    )
    assert isinstance(config_node, ast.Call)
    assert isinstance(config_node.func, ast.Name) and config_node.func.id == "StrategyConfig"
    child["strategy_config"] = eval(
        compile(ast.Expression(config_node), str(folder / "TS_Wavelet.py"), "eval"), common
    )
    parent_type = type("SourceTSPredictFeatureProjection", (), parent)
    child_type = type("SourceTSWaveletCPUProjection", (parent_type,), child)
    owner = child_type()
    owner.dataframeUtils = type("SourceRollingNormalizer", (), normalizer)()
    owner.custom_trade_info = {}
    owner.curr_pair = "SOURCE_NUMERICAL_REFERENCE"
    owner.forecaster = forecasters.sgd_forecaster()
    owner.forecaster.set_detrend(True)
    owner.wavelet_type = wavelets.WaveletType.DWT
    owner.wavelet = wavelets.make_wavelet(owner.wavelet_type)
    owner.train_min_len = owner.wavelet_size
    owner.model_window = owner.wavelet_size
    owner.use_mlx = False  # Frozen CPU research variant, not Apple MLX parity.
    assert not owner.scale_results and owner.strategy_config.scale_results
    assert owner.forecaster.requires_pretraining()
    assert owner.forecaster.supports_multiple_columns()
    assert not scaler.scaler_exists(owner.get_storage_location(), "main_scaler")
    assert not Path(owner.get_storage_location()).exists()
    return owner
