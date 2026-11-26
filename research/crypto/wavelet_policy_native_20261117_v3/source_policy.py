"""Pinned source definitions bridged to the official native strategy lifecycle.

Unrelated top-level MLX/TensorFlow/finta imports are excluded, not mocked.
All source class and method bodies remain intact except an explicit CPU flag.
No financial engine, fill simulator, or replacement indicator is provided here.
"""

# ruff: noqa: S101, S102
# Executed source definitions are hash-checked and recorded in the projection receipt.
import ast
import copy
import hashlib
import importlib.util
import json
import logging
import math
import multiprocessing
import os
import pickle
import platform
import sys
import traceback
import types
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum, auto
from functools import reduce
from pathlib import Path
from typing import Any

from research.crypto.wavelet_feature_native_20261117_v2.source_reference import bootstrap


HERE = Path(__file__).resolve().parent
FEATURE = HERE.with_name("wavelet_feature_native_20261117_v2")
ORIGINAL = HERE.with_name("wavelet_source_native_20261117_v1")
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
PROJECTION_RECEIPTS = []


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_inputs():
    for filename, digest in json.loads((HERE / "protocol.json").read_text())["inputs"].items():
        assert sha(filename) == digest, filename
        for row in json.loads(Path(filename).read_text())["files"]:
            assert sha(row["path"]) == row["sha256"], row["path"]
    for row in json.loads((HERE / "source_receipts.json").read_text()):
        assert sha(row["local_path"]) == row["sha256"]


def source_definitions(path, namespace, *, cpu_class=None, only_names=None):
    """Execute actual class/function definitions with real supplied dependencies."""
    node = ast.parse(path.read_text())
    selected = [n for n in node.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
    if only_names is not None:
        selected = [n for n in selected if n.name in only_names]
        assert {n.name for n in selected} == set(only_names)
    modifications = []
    if cpu_class:
        cls = next(c for c in selected if isinstance(c, ast.ClassDef) and c.name == cpu_class)
        stmt = next(
            s
            for s in cls.body
            if isinstance(s, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "use_mlx" for t in s.targets)
        )
        modifications.append(
            {
                "line": stmt.lineno,
                "original": ast.unparse(stmt),
                "replacement": "use_mlx = False",
                "reason": "Frozen CPU branch",
            }
        )
        stmt.value = ast.Constant(False)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    name = "_wavelet_policy_source_" + path.stem
    module = types.ModuleType(name)
    module.__dict__.update(namespace)
    module.__dict__.update(__name__=name, __file__=str(path))
    sys.modules[name] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    PROJECTION_RECEIPTS.append(
        {
            "path": str(path),
            "sha256": sha(path),
            "selected_definitions": [n.name for n in selected],
            "compiled_definition_ast_sha256": hashlib.sha256(ast.dump(tree).encode()).hexdigest(),
            "modifications": modifications,
            "top_level_imports_and_side_effects_not_executed": True,
            "annotations_deferred_for_import_isolation": True,
        }
    )
    return module


def import_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_policy_class():
    checked_inputs()
    sys.path.insert(0, str(ENGINE))
    namespace = bootstrap()
    import joblib
    import talib.abstract as ta
    from sklearn.base import BaseEstimator
    from sklearn.preprocessing import LabelEncoder, MinMaxScaler, StandardScaler

    import freqtrade
    import freqtrade.vendor.qtpylib.indicators as qtpylib
    from freqtrade.persistence import Trade
    from freqtrade.strategy import CategoricalParameter, DecimalParameter, IntParameter, IStrategy

    assert Path(freqtrade.__file__).resolve() == ENGINE / "freqtrade/__init__.py"
    namespace.update(
        {
            "ABC": ABC,
            "abstractmethod": abstractmethod,
            "BaseEstimator": BaseEstimator,
            "CategoricalParameter": CategoricalParameter,
            "DecimalParameter": DecimalParameter,
            "IntParameter": IntParameter,
            "IStrategy": IStrategy,
            "Trade": Trade,
            "Enum": Enum,
            "IntEnum": IntEnum,
            "auto": auto,
            "dataclass": dataclass,
            "field": field,
            "datetime": datetime,
            "timedelta": timedelta,
            "timezone": timezone,
            "reduce": reduce,
            "Path": Path,
            "Any": Any,
            "math": math,
            "os": os,
            "sys": sys,
            "copy": copy,
            "joblib": joblib,
            "pickle": pickle,
            "traceback": traceback,
            "warnings": warnings,
            "log": logging.getLogger(__name__),
            "multiprocessing": multiprocessing,
            "platform": platform,
            "freqtrade": freqtrade,
            "ta": ta,
            "qtpylib": qtpylib,
            "Series": namespace["pd"].Series,
            "StandardScaler": StandardScaler,
            "MinMaxScaler": MinMaxScaler,
            "LabelEncoder": LabelEncoder,
        }
    )
    utils = source_definitions(FEATURE / "sources/utils/DataframeUtils.py", namespace)
    namespace.update(DataframeUtils=utils.DataframeUtils, ScalerType=utils.ScalerType)
    custom = import_file(
        "_wavelet_policy_custom_indicators", HERE / "sources/utils/custom_indicators.py"
    )
    namespace["cta"] = custom
    populator = source_definitions(FEATURE / "sources/utils/DataframePopulator.py", namespace)
    namespace.update(
        DataframePopulator=populator.DataframePopulator, DatasetType=populator.DatasetType
    )
    diagnostics = source_definitions(
        FEATURE / "sources/Framework/StrategyDiagnostics.py", namespace
    )
    namespace["StrategyDiagnostics"] = diagnostics.StrategyDiagnostics
    environment = source_definitions(FEATURE / "sources/utils/Environment.py", namespace)
    namespace["Environment"] = environment.Environment
    exits = import_file("_wavelet_policy_exit_policy", HERE / "sources/utils/ExitPolicy.py")
    for name in [
        "ExitParams",
        "atr_stop_fraction",
        "atr_take_profit",
        "custom_exit_decision",
        "discretionary_exit_allowed",
        "rolling_rvol",
        "STOP_AT_MARKET_RATIO",
    ]:
        namespace[name] = getattr(exits, name)
    gan = source_definitions(FEATURE / "sources/GANs/GANType.py", namespace)
    namespace["GANType"] = gan.GANType
    base = source_definitions(FEATURE / "sources/Framework/BaseStrategy.py", namespace)
    for name in ["BaseStrategy", "StrategyConfig", "NormalizationType", "ModelType"]:
        namespace[name] = getattr(base, name)
    detrenders = source_definitions(FEATURE / "sources/utils/Detrenders.py", namespace)
    namespace["Detrenders"] = detrenders
    forecasters = source_definitions(
        ORIGINAL / "sources/nateemma_strategies/utils/Forecasters.py", namespace
    )
    wavelets = source_definitions(
        ORIGINAL / "sources/nateemma_strategies/utils/Wavelets.py", namespace
    )
    namespace.update(Forecasters=forecasters, Wavelets=wavelets)
    scalers = source_definitions(FEATURE / "sources/utils/Scalers.py", namespace)
    for name in ["load_scaler", "save_scaler", "scaler_exists"]:
        namespace[name] = getattr(scalers, name)
    folder = ORIGINAL / "sources/nateemma_strategies/TSPredict"
    parent = source_definitions(folder / "TSPredict.py", namespace, only_names={"TSPredict"})
    namespace["TSPredict"] = parent.TSPredict
    child = source_definitions(
        folder / "TS_Wavelet.py", namespace, cpu_class="TS_Wavelet", only_names={"TS_Wavelet"}
    )
    namespace["TS_Wavelet"] = child.TS_Wavelet
    concrete = source_definitions(folder / "TS_Wavelet_DWT.py", namespace)
    cls = concrete.TS_Wavelet_DWT
    assert not cls.strategy_config.aggregate_pairs
    assert not cls.use_mlx and not cls.scale_results
    return cls
