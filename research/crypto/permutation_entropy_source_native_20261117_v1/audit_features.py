"""Independent full-history source-function/gate audit, never an account calculator."""

# ruff: noqa: S101, S102
# Assertions and pinned source-only AST functions are intentional audit operations.
import ast
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
if not __debug__:
    raise RuntimeError("Feature audit requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def main():
    plan = read(HERE / "plan.json")
    output = Path(plan["output_root"])
    for r in read(HERE / "source_receipts.json")["files"]:
        raw = Path(r["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == r["sha256"]
        assert (
            hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
            ).hexdigest()
            == r["git_blob"]
        )
    path = HERE / "source/perm_entropy.py"
    functions = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)]
    ns = {"np": np, "pd": pd, "math": math}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), ns)
    config = read(plan["candidates"][0]["config"]["path"])
    prices = pd.read_parquet(config["signal_prices"])
    availability = prices.index + pd.Timedelta(hours=1)
    source_entropy = pd.DataFrame(
        {p: ns["permutation_entropy"](prices[p].to_numpy(), 3, 28) for p in prices},
        index=availability,
    )
    median = source_entropy.shift(1).rolling(8760, min_periods=8760).median()
    gate = source_entropy.le(median) & median.notna() & source_entropy.notna()
    old_plan = read(HERE.with_name("trendcore_source_native_20261117_v1") / "plan.json")
    base_path = (
        Path(old_plan["output_root"])
        / "preflight_trendcore_1000_conservative_admission1/native_weights.parquet"
    )
    base = pd.read_parquet(base_path)
    expected = base * gate.reindex(base.index).fillna(False)
    native_preflight = pd.read_parquet(
        output / "preflight_entropytrend_1000_conservative_admission1/native_weights.parquet"
    )
    pd.testing.assert_frame_equal(expected, native_preflight, check_freq=False)
    checked = 0
    for candidate in plan["candidates"]:
        directory = output / (candidate["name"] + f"_{candidate['cost_bps']}bps")
        with gzip.open(directory / "strategy_trace.json.gz", "rt") as handle:
            trace = json.load(handle)
        for key, reference in [("base_weights", base), ("weights", expected)]:
            actual = pd.DataFrame(trace[key]).set_index("day")
            actual.index = pd.to_datetime(actual.index, utc=True)
            pd.testing.assert_frame_equal(actual, reference, check_names=False, check_freq=False)
        for row in trace["entropy"]:
            clock, pair = pd.Timestamp(row["clock"]), row["pair"]
            for field, reference in [
                ("entropy", source_entropy),
                ("preceding_year_median", median),
            ]:
                wanted = reference.at[clock, pair]
                if pd.isna(wanted):
                    assert row[field] is None
                else:
                    assert abs(wanted - row[field]) < 2e-15
            assert row["gate"] == bool(gate.at[clock, pair])
            checked += 1
    window = expected.loc[plan["start"] : plan["comparison_end"]].index
    activity = {}
    for pair in prices:
        before = base.loc[window, pair]
        after = expected.loc[window, pair]
        activity[pair] = {
            "execution_targets": len(window),
            "base_positive_targets": int(before.gt(0).sum()),
            "retained_positive_targets": int(after.gt(0).sum()),
            "entropy_gate_on_targets": int(gate.loc[window, pair].sum()),
            "zeroed_positive_targets": int((before.gt(0) & after.eq(0)).sum()),
        }
    result = {
        "passed": True,
        "source_functions_full_history": True,
        "financial_engine_executed": False,
        "native_trace_rows_checked": checked,
        "history_rows_per_asset": len(prices),
        "source_first_valid_entropy": str(source_entropy.dropna().index[0]),
        "local_threshold_first_valid": str(median.dropna().index[0]),
        "source_is_indicator_only": True,
        "volume_entropy_not_selected": True,
        "activity": activity,
        "input_price_sha256": hashlib.sha256(
            Path(config["signal_prices"]).read_bytes()
        ).hexdigest(),
    }
    (HERE / "feature_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
