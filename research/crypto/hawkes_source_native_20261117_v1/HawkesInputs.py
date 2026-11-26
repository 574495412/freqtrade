"""Pinned author Hawkes features, with a fixed local ATR dependency branch.

The source functions are executed unchanged. Only their two function definitions
are loaded; the author's plotting and forward-return diagnostics are not run.
The dependency choice below is a local reproducibility contract, not a claim
about the author's unrecorded 2023 environment.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta
import talib


# The native runner flattens source files into its strategies directory. A text
# suffix prevents Freqtrade from importing the author's diagnostic top-level code.
_NATIVE_SOURCE_PATH = Path(__file__).with_name("source_hawkes.txt")
SOURCE_PATH = (
    _NATIVE_SOURCE_PATH
    if _NATIVE_SOURCE_PATH.exists()
    else Path(__file__).parent / "source" / "hawkes.py"
)
SOURCE_SHA256 = "004d1a46777fa7d70a3fa97484fdc9329fd369fc61660a913bb1980991e47cdf"
SOURCE_COMMIT = "51c8557eb078f1be368e1ff82c53c9118685e7de"
ATR_LENGTH = 336
KAPPA = 0.1
QUANTILE_LOOKBACK = 168
EXPECTED_VERSIONS = {
    "pandas_ta": "0.3.16",
    "talib": "0.8.2",
    "pandas": "3.0.6",
    "numpy": "2.5.4",
}


def dependency_contract() -> dict:
    """Fail closed if the selected pandas_ta default branch can change."""
    actual = {
        "pandas_ta": ta.__version__,
        "talib": talib.__version__,
        "pandas": pd.__version__,
        "numpy": np.__version__,
    }
    if actual != EXPECTED_VERSIONS or ta.Imports.get("talib") is not True:
        raise RuntimeError(f"Frozen ATR dependency contract differs: {actual}")
    return {
        "versions": actual,
        "pandas_ta_default_atr_branch": "TA-Lib ATR",
        "talib_available": True,
        "author_original_environment_reproduced": False,
    }


def load_source_functions() -> dict:
    """Load only hash-verified author feature and state-machine functions."""
    raw = SOURCE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Author hawkes.py SHA256 differs from the frozen source")
    tree = ast.parse(raw.decode("utf-8"), filename=str(SOURCE_PATH))
    names = {"hawkes_process", "vol_signal"}
    functions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if len(functions) != 2 or {node.name for node in functions} != names:
        raise RuntimeError("Pinned source feature functions are missing or duplicated")
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {"np": np, "pd": pd}
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)  # noqa: S102 - hash-pinned functions only
    return {name: namespace[name] for name in names}


def validate_input(frame: pd.DataFrame) -> None:
    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing native OHLC fields: {sorted(required - set(frame.columns))}")
    if len(frame) <= ATR_LENGTH:
        raise ValueError("At least 337 one-hour observations are required")
    dates = frame["date"]
    if not isinstance(dates.dtype, pd.DatetimeTZDtype) or str(dates.dt.tz) != "UTC":
        raise ValueError("date must be timezone-aware UTC datetime values")
    if dates.isna().any() or not dates.eq(dates.dt.floor("h")).all():
        raise ValueError("date must contain completed whole-hour candle open times")
    if not dates.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Input must be increasing, unique and uninterrupted at one hour")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or not (prices > 0).all():
        raise ValueError("OHLC prices must be finite and strictly positive")
    if not (
        frame["high"].ge(frame[["open", "low", "close"]].max(axis=1)).all()
        and frame["low"].le(frame[["open", "high", "close"]].min(axis=1)).all()
    ):
        raise ValueError("OHLC high/low bounds are inconsistent")


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Return unfilled author ATR, normalised range, intensity and signed state.

    All four outputs use the same candle index as the input. A caller may only
    execute after the corresponding candle closes. Zero ATR remains undefined
    range/NaN intensity, with the source state machine deciding its own state.
    """
    validate_input(frame)
    dependency_contract()
    source = load_source_functions()
    atr = ta.atr(
        np.log(frame["high"]),
        np.log(frame["low"]),
        np.log(frame["close"]),
        ATR_LENGTH,
    )
    norm_range = (np.log(frame["high"]) - np.log(frame["low"])) / atr
    intensity = source["hawkes_process"](norm_range, KAPPA)
    signal = source["vol_signal"](frame["close"], intensity, QUANTILE_LOOKBACK)
    return pd.DataFrame(
        {"atr": atr, "norm_range": norm_range, "v_hawk": intensity, "signal": signal},
        index=frame.index,
    )
