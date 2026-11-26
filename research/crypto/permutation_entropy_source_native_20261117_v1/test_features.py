"""Source parity, ties, availability clocks and actual-data causality."""

# ruff: noqa: S101, S102
# Test assertions and source-only AST definitions; no source main or financial simulator.

import ast
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.data.ordinal_entropy import entropy_gate, ordinal_patterns, permutation_entropy


HERE = Path(__file__).resolve().parent


def source_functions():
    path = HERE / "source/perm_entropy.py"
    tree = ast.parse(path.read_text())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    ns = {"np": np, "pd": pd, "math": math}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)
    return ns


def prices():
    old = HERE.with_name("trendcore_source_native_20261117_v1")
    source = json.loads((old / "input_receipts.json").read_text())["signal_prices"]["path"]
    return pd.read_parquet(source)


def test_source_parity_including_ties():
    source = source_functions()
    rng = np.random.default_rng(32628)
    for values in [rng.normal(size=1200), rng.integers(0, 5, 1200), np.ones(1000)]:
        np.testing.assert_equal(ordinal_patterns(values), source["ordinal_patterns"](values, 3))
        np.testing.assert_allclose(
            permutation_entropy(values),
            source["permutation_entropy"](values, 3, 28),
            atol=2e-15,
            rtol=2e-15,
            equal_nan=True,
        )
    assert np.isnan(permutation_entropy(np.arange(171.0))[:170]).all()
    assert permutation_entropy(np.arange(171.0))[170] == 0


def test_actual_source_parity_and_prefix():
    data = prices().iloc[-2400:]
    source = source_functions()
    for pair in data:
        full = permutation_entropy(data[pair].to_numpy())
        np.testing.assert_allclose(
            full,
            source["permutation_entropy"](data[pair].to_numpy(), 3, 28),
            atol=2e-15,
            rtol=2e-15,
            equal_nan=True,
        )
        for end in [171, 501, 1700]:
            np.testing.assert_allclose(
                full[:end],
                permutation_entropy(data[pair].iloc[:end]),
                atol=2e-15,
                rtol=2e-15,
                equal_nan=True,
            )


def test_known_clock_previous_only_threshold_and_future_shock():
    data = prices()
    full = entropy_gate(data)
    assert full[0].index[0] == data.index[0] + pd.Timedelta(hours=1)
    assert not full[2].iloc[:8930].any().any()
    for end in [10000, 16001, len(data) - 24]:
        prefix = entropy_gate(data.iloc[:end])
        for actual, expected in zip(prefix, full, strict=True):
            pd.testing.assert_frame_equal(actual, expected.iloc[:end])
        shocked = data.copy()
        shocked.iloc[end:] *= np.linspace(10, 100, len(data) - end)[:, None]
        for actual, expected in zip(entropy_gate(shocked), full, strict=True):
            pd.testing.assert_frame_equal(actual.iloc[:end], expected.iloc[:end])
    row = 10000
    np.testing.assert_allclose(full[1].iloc[row], full[0].iloc[row - 8760 : row].median())


def test_missing_invalid_input_rejected():
    data = prices().iloc[:1000]
    with pytest.raises(ValueError, match="contiguous"):
        entropy_gate(data.drop(data.index[20]))
    invalid = data.copy()
    invalid.iloc[10, 0] = np.nan
    with pytest.raises(ValueError, match="Finite"):
        entropy_gate(invalid)
    with pytest.raises(ValueError, match="preregistered"):
        permutation_entropy(data.iloc[:, 0], multiplier=27)
