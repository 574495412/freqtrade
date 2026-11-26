"""Hash-pinned original head/shoulders recognition, without account signals."""

from __future__ import annotations

import ast
import hashlib
import sys
import types
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List  # noqa: UP035 - exact original annotation dependency

import numpy as np
import pandas as pd


STUDY = Path(__file__).parent
SOURCE_HASHES = {
    "head_shoulders.py": "8dbcde6cc51b7dc7e8cd56d0491fc28cada901e98894cb74ad27d4b835f9a0bd",
    "rolling_window.py": "58ea065d44bf1136fb6d31791e8398f4c4a4417b8d03120aeeea5cfdfcf8d480",
}
SELECTED = {
    "rolling_window.py": {"rw_top", "rw_bottom"},
    "head_shoulders.py": {
        "HSPattern",
        "compute_pattern_r2",
        "check_hs_pattern",
        "check_ihs_pattern",
        "find_hs_patterns",
        "get_pattern_return",
    },
}
INDEX_FIELDS = {
    "l_shoulder",
    "r_shoulder",
    "l_armpit",
    "r_armpit",
    "head",
    "start_i",
    "break_i",
    "head_width",
}


def load_author():
    """Execute selected exact AST nodes only; plotting/main code is excluded."""
    module = types.ModuleType("_pinned_neurotrader_head_shoulders")
    module.__dict__.update(
        {"np": np, "pd": pd, "List": List, "deque": deque, "dataclass": dataclass}  # noqa: UP006
    )
    sys.modules[module.__name__] = module
    manifests = []
    for filename in ["rolling_window.py", "head_shoulders.py"]:
        path = STUDY / "source" / filename
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_HASHES[filename]:
            raise ValueError(f"Frozen source changed: {filename}")
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        nodes = [
            node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in SELECTED[filename]
        ]
        if (
            len(nodes) != len(SELECTED[filename])
            or {node.name for node in nodes} != SELECTED[filename]
        ):
            raise ValueError(f"Selected source nodes missing or duplicated: {filename}")
        for node in nodes:
            manifests.append(
                {
                    "source": filename,
                    "name": node.name,
                    "original_ast_sha256": hashlib.sha256(
                        ast.dump(node, include_attributes=False).encode()
                    ).hexdigest(),
                    "source_line": node.lineno,
                    "source_end_line": node.end_lineno,
                }
            )
        exact_module = ast.Module(body=nodes, type_ignores=[])
        exec(compile(exact_module, str(path), "exec", dont_inherit=True), module.__dict__)  # noqa: S102
    return module, manifests


def validate_frame(frame):
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame) or frame.empty:
        raise ValueError("Nonempty complete native OHLCV is required")
    dates = frame.date
    if not isinstance(dates.dtype, pd.DatetimeTZDtype) or str(dates.dt.tz) != "UTC":
        raise ValueError("Candle open times must be timezone-aware UTC")
    if dates.isna().any() or not dates.eq(dates.dt.floor("h")).all():
        raise ValueError("Invalid candle timestamp")
    if not dates.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Hourly candle calendar is not continuous and unique")
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("Nonfinite/nonpositive prices or negative volumes")
    if not (
        frame.high.ge(frame[["open", "low", "close"]].max(axis=1)).all()
        and frame.low.le(frame[["open", "high", "close"]].min(axis=1)).all()
    ):
        raise ValueError("Invalid OHLC bounds")


def extract_events(frame, author):
    """Original recognition on log-close with source main's order6/earlyFalse."""
    validate_frame(frame)
    close = np.log(frame.close.to_numpy(dtype=float))
    normal, inverse = author.find_hs_patterns(close, 6, early_find=False)
    rows = []
    for branch, patterns in [("head_shoulders", normal), ("inverse_head_shoulders", inverse)]:
        for ordinal, pattern in enumerate(patterns):
            row = {}
            for key, value in asdict(pattern).items():
                row[key] = (
                    bool(value)
                    if key == "inverted"
                    else int(value)
                    if key in INDEX_FIELDS
                    else float(value)
                )
            target = (
                pattern.neck_end + pattern.head_height
                if pattern.inverted
                else pattern.neck_end - pattern.head_height
            )
            stop = pattern.r_shoulder_p
            self_terminal = (
                (pattern.break_p > target or pattern.break_p < stop)
                if pattern.inverted
                else (pattern.break_p < target or pattern.break_p > stop)
            )
            row.update(
                {
                    "source_branch": branch,
                    "source_branch_ordinal": ordinal,
                    "confirmation_bar_open": frame.date.iloc[pattern.break_i],
                    "earliest_signal_available": frame.date.iloc[pattern.break_i]
                    + pd.Timedelta(hours=1),
                    "source_stop_log_price": float(stop),
                    "source_target_log_price": float(target),
                    "source_expiry_i": int(pattern.break_i + pattern.head_width - 1),
                    "self_terminal_at_confirmation": bool(self_terminal),
                }
            )
            rows.append(row)
    if not rows:
        columns = {}
        for key in asdict(author.HSPattern(inverted=False)):
            dtype = "bool" if key == "inverted" else "int64" if key in INDEX_FIELDS else "float64"
            columns[key] = pd.Series(dtype=dtype)
        for key in ["source_branch"]:
            columns[key] = pd.Series(dtype="object")
        for key in ["source_branch_ordinal", "source_expiry_i"]:
            columns[key] = pd.Series(dtype="int64")
        for key in ["confirmation_bar_open", "earliest_signal_available"]:
            columns[key] = pd.Series(dtype="datetime64[ns, UTC]")
        for key in ["source_stop_log_price", "source_target_log_price"]:
            columns[key] = pd.Series(dtype="float64")
        columns["self_terminal_at_confirmation"] = pd.Series(dtype="bool")
        return pd.DataFrame(columns)
    return (
        pd.DataFrame(rows)
        .sort_values(["break_i", "inverted", "source_branch_ordinal"])
        .reset_index(drop=True)
    )
