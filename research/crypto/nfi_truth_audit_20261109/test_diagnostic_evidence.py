# ruff: noqa: S101, S102
"""Regression evidence for replay isolation and strict natural-exit matching."""

import ast
import json
from pathlib import Path

import pandas as pd
import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001"


def test_terminal_force_close_does_not_satisfy_target():
    tree = ast.parse((HERE / "diagnostic.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "strict_target")
    namespace = {"pd": pd}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<strict_target>", "exec"), namespace)
    record = {
        "pair": "ADA/USDT",
        "open_date": "2025-01-01T00:00Z",
        "close_date": "2025-01-02T00:00Z",
        "exit_reason": "force_exit",
    }
    args = ([record], record["pair"], record["open_date"], record["close_date"])
    assert namespace["strict_target"](*args) == []
    record["exit_reason"] = "natural_signal"
    assert namespace["strict_target"](*args) == [record]


@pytest.mark.parametrize(
    "market,fixed", [("spot", "spot-fixed-offline"), ("futures", "futures-fixed")]
)
def test_actual_native_baseline_preserved_and_prefix_cache_causal(market, fixed):
    prior = OUT / f"{market}-v002"
    assert (prior / "call-000/trades.json").read_bytes() == (
        OUT / fixed / "call-000/trades.json"
    ).read_bytes()
    rows = json.loads((prior / "matrix.json").read_text())
    assert len(rows) == 12
    assert {r["scope"] for r in rows} == {"single", "all"}
    for row in rows:
        assert row["target_natural_exit_reproduced"] == (row["cache"] == "clean")
        assert row["same_exit_reason"] == (row["cache"] == "clean")
        for value in row["native_call"]["indicator_comparison"].values():
            assert value["different_rows"] == 0
    frame = pd.read_csv(OUT / fixed / "official.csv")
    assert not frame.has_bias.any()
    assert frame.biased_entry_signals.sum() == frame.biased_exit_signals.sum() == 0
