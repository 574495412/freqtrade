"""Input, independent arithmetic and source-causality checks; no account PnL."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pandas_ta as ta

from . import HawkesInputs as candidate


STUDY = Path(__file__).parent
ROOT = STUDY.parents[2]
DATA = ROOT / "user_data/crypto_quant/nfi_carry_matched_native_20261115_v1/candles/futures"


def original_features(frame):
    """Separate AST selection of source functions and original top-level formulas."""
    source = ast.parse(candidate.SOURCE_PATH.read_text())
    allowed_functions = {"hawkes_process", "vol_signal"}
    selected = [
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name in allowed_functions
    ]
    # Execute the source's own top-level feature assignments, not our formulas.
    for node in source.body:
        if isinstance(node, ast.Assign):
            target = ast.unparse(node.targets[0])
            if target in {
                "norm_lookback",
                "data['atr']",
                "data['norm_range']",
                "data['v_hawk']",
                "data['sig']",
            }:
                selected.append(node)
    namespace = {"np": np, "pd": pd, "ta": ta, "data": frame.copy(deep=True)}
    exec(  # noqa: S102 - original source nodes, verified by the candidate hash gate
        compile(ast.Module(body=selected, type_ignores=[]), "author-selected-ast", "exec"),
        namespace,
    )
    return namespace["data"][["atr", "norm_range", "v_hawk", "sig"]].rename(
        columns={"sig": "signal"}
    )


def independent_features(frame):
    """NumPy Wilder ATR and finite-memory event state, without source callbacks."""
    high = np.log(frame.high.to_numpy())
    low = np.log(frame.low.to_numpy())
    close_log = np.log(frame.close.to_numpy())
    count = len(frame)
    true_range = np.full(count, np.nan)
    true_range[1:] = np.maximum.reduce(
        [high[1:] - low[1:], abs(high[1:] - close_log[:-1]), abs(low[1:] - close_log[:-1])]
    )
    atr = np.full(count, np.nan)
    atr[336] = np.sum(true_range[1:337]) / 336
    for index in range(337, count):
        atr[index] = (atr[index - 1] * 335 + true_range[index]) / 336
    with np.errstate(divide="ignore", invalid="ignore"):
        ranges = (high - low) / atr
    accumulator = np.full(count, np.nan)
    state = np.nan
    decay = np.exp(-0.1)
    for index in range(1, count):
        state = ranges[index] if np.isnan(state) else state * decay + ranges[index]
        accumulator[index] = state * 0.1
    quantiles = np.full((count, 2), np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(accumulator, 168)
    quantiles[167:] = np.quantile(windows, [0.05, 0.95], axis=1).T
    signal = np.zeros(count)
    calm_index = None
    position = 0
    close = frame.close.to_numpy()
    for index in range(count):
        if accumulator[index] < quantiles[index, 0]:
            calm_index = index
            position = 0
        if (
            index > 0
            and accumulator[index] > quantiles[index, 1]
            and accumulator[index - 1] <= quantiles[index - 1, 1]
            and calm_index is not None
            and calm_index > 0
        ):
            position = 1 if close[index] > close[calm_index] else -1
        signal[index] = position
    return pd.DataFrame(
        {"atr": atr, "norm_range": ranges, "v_hawk": accumulator, "signal": signal},
        index=frame.index,
    )


def compare_arithmetic(actual, expected):
    errors = {}
    for column in ["atr", "norm_range", "v_hawk"]:
        np.testing.assert_allclose(
            actual[column], expected[column], rtol=1e-12, atol=1e-12, equal_nan=True
        )
        delta = np.abs(actual[column].to_numpy() - expected[column].to_numpy())
        errors[column] = float(np.nanmax(delta))
    np.testing.assert_array_equal(actual.signal, expected.signal)
    return errors


def check_rejections(frame):
    cases = {}
    cases["missing_field"] = frame.drop(columns="high")
    cases["non_utc"] = frame.assign(date=frame.date.dt.tz_convert("Asia/Shanghai"))
    cases["naive_clock"] = frame.assign(date=frame.date.dt.tz_localize(None))
    cases["gap"] = frame.drop(frame.index[100])
    cases["reversed_clock"] = frame.iloc[::-1]
    cases["negative_price"] = frame.assign(close=-frame.close)
    cases["invalid_bounds"] = frame.assign(high=frame.low * 0.5)
    cases["short_history"] = frame.iloc[:336]
    cases["off_hour"] = frame.assign(date=frame.date + pd.Timedelta(minutes=1))
    invalid = frame.copy()
    invalid.loc[invalid.index[100], "close"] = np.nan
    cases["nan_price"] = invalid
    for name, invalid in cases.items():
        try:
            candidate.build_features(invalid)
        except ValueError:
            continue
        raise AssertionError(f"Invalid input admitted: {name}")
    with patch.object(candidate, "SOURCE_SHA256", "0" * 64):
        try:
            candidate.build_features(frame)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Source hash change admitted")
    with patch.dict(ta.Imports, {"talib": False}):
        try:
            candidate.build_features(frame)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Alternative ATR dependency branch admitted")
    return list(cases) + ["source_hash", "atr_dependency_branch"]


def check_native_flattened_path(frame):
    """A relocated strategy helper must load only its adjacent text source."""
    with tempfile.TemporaryDirectory(prefix="hawkes_source_path_") as directory:
        isolated = Path(directory)
        module_path = isolated / "HawkesInputs.py"
        shutil.copyfile(STUDY / "HawkesInputs.py", module_path)
        (isolated / "source_hawkes.txt").write_bytes(candidate.SOURCE_PATH.read_bytes())
        spec = importlib.util.spec_from_file_location("isolated_hawkes_inputs", module_path)
        if spec is None or spec.loader is None:
            raise AssertionError("Cannot load the isolated strategy helper")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.SOURCE_PATH != isolated / "source_hawkes.txt":
            raise AssertionError("The native helper did not select its adjacent source")
        if (isolated / "source").exists():
            raise AssertionError("The path test unexpectedly contains a source subdirectory")
        pd.testing.assert_frame_equal(
            module.build_features(frame), candidate.build_features(frame), check_exact=True
        )
    return {
        "only_helper_and_adjacent_text_source_required": True,
        "features_exact": True,
        "source_subdirectory_present": False,
    }


def main():
    records = []
    for asset in ["BTC", "ETH"]:
        path = DATA / f"{asset}_USDT_USDT-1h-futures.feather"
        frame = pd.read_feather(path)
        original = frame.copy(deep=True)
        actual = candidate.build_features(frame)
        pd.testing.assert_frame_equal(frame, original)
        pd.testing.assert_frame_equal(actual, original_features(frame), check_exact=True)
        errors = compare_arithmetic(actual, independent_features(frame))
        split = len(frame) - 1000
        pd.testing.assert_frame_equal(
            actual.iloc[:split], candidate.build_features(frame.iloc[:split]), check_exact=True
        )
        shocked = frame.copy(deep=True)
        shocked.loc[shocked.index[split:], ["open", "high", "low", "close"]] *= 4.0
        pd.testing.assert_frame_equal(
            actual.iloc[:split], candidate.build_features(shocked).iloc[:split], check_exact=True
        )
        records.append(
            {
                "asset": asset,
                "input": str(path),
                "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": len(frame),
                "source_outputs_exact": True,
                "independent_max_errors": errors,
                "independent_signed_states_exact": True,
                "prefix_and_future_shock_rows": split,
                "prefix_and_future_shock_exact": True,
                "input_unchanged": True,
                "signed_state_counts": {
                    str(k): int(v) for k, v in actual.signal.value_counts().items()
                },
            }
        )
    flat = frame.iloc[:1000].copy()
    flat[["open", "high", "low", "close"]] = 100.0
    flat_features = candidate.build_features(flat)
    pd.testing.assert_frame_equal(flat_features, original_features(flat), check_exact=True)
    if not flat_features.signal.eq(0).all():
        raise AssertionError("Flat prices generated a position")
    if not flat_features.atr.iloc[336:].eq(0).all():
        raise AssertionError("Flat prices changed zero ATR")
    if not (flat_features.norm_range.isna().all() and flat_features.v_hawk.isna().all()):
        raise AssertionError("Undefined zero-ATR values were filled")
    failures = check_rejections(frame.iloc[:1000])
    isolated_path = check_native_flattened_path(frame.iloc[:1000])
    receipt = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "scope": "Source feature and causality validation only; no account economics",
        "source_commit": candidate.SOURCE_COMMIT,
        "source_sha256": candidate.SOURCE_SHA256,
        "dependencies": candidate.dependency_contract(),
        "records": records,
        "flat_zero_atr_preserves_source_nan_and_zero_entry_state": True,
        "invalid_inputs_and_integrity_changes_rejected": failures,
        "native_flattened_path_check": isolated_path,
        "revision": "Only source-file location adapted; source calculations unchanged",
        "prior_validation_preserved": {
            "path": str(STUDY / "feature_validation.json"),
            "sha256": hashlib.sha256((STUDY / "feature_validation.json").read_bytes()).hexdigest(),
            "previous_helper_sha256": (
                "876d709fcdbb4c8b3683dc2baf334c34c2950fc269ff7aab745750898f0f8b29"
            ),
            "previous_test_sha256": (
                "3bdd19a3b6392af74ac6b515d00dcc7b67c0f069c31040b09640b232327df70d"
            ),
        },
        "code_sha256": {
            name: hashlib.sha256((STUDY / name).read_bytes()).hexdigest()
            for name in ["HawkesInputs.py", "test_inputs.py"]
        },
        "financial_account_completed": False,
        "higher_return_proven": False,
    }
    output = STUDY / "feature_validation_v2.json"
    with output.open("x") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
