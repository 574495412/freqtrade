"""Prepare a new mechanism panel, retaining original frozen labels byte-for-byte."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.conditional_context import context_features
from research.crypto.conditional_factors import expanded_groups


def verify_input(path, expected):
    if sha(path / "receipts.json") != expected:
        raise ValueError("Frozen source receipt changed")
    for name, digest in read(path / "receipts.json").items():
        if sha(path / name) != digest:
            raise ValueError(f"Source input changed: {name}")


def prepare(plan_path, output):
    if output.exists():
        raise ValueError("Preserve previous context panel")
    plan = read(plan_path)
    base, metrics = Path(plan["base_feature_panel"]), Path(plan["metrics_dataset"])
    for path, expected in [
        (base, plan["base_feature_panel_receipts_sha256"]),
        (metrics, plan["metrics_receipts_sha256"]),
    ]:
        verify_input(path, expected)
    native = Path(plan["base_dataset"])
    if sha(native / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Native price source identity mismatch")
    receipts = read(native / "receipts.json")
    output.mkdir(parents=True)
    (output / "sources").mkdir()
    for name in [
        "conditional_context.py",
        "prepare_conditional_context.py",
        "conditional_factors.py",
        "conditional_learning.py",
    ]:
        path = Path(__file__).with_name(name)
        (output / "sources" / name).write_bytes(path.read_bytes())
    save(output / "plan.json", plan)
    save(
        output / "input_freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "source_sha256": {p.name: sha(p) for p in (output / "sources").iterdir()},
            "prior_panel_receipts_sha256": sha(base / "receipts.json"),
            "metrics_receipts_sha256": sha(metrics / "receipts.json"),
            "outcome_changes": False,
            "orders_enabled": False,
        },
    )
    panels, closes, raw_metrics = {}, {}, {}
    start, end = (
        pd.Timestamp(plan["feature_start"], tz="UTC"),
        pd.Timestamp(plan["feature_end"], tz="UTC"),
    )
    for symbol in plan["symbols"]:
        panels[symbol] = pd.read_parquet(base / f"{symbol}.parquet")
        raw_metrics[symbol] = pd.read_parquet(metrics / f"{symbol}.parquet")
        filename = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        if sha(native / filename) != receipts[filename]:
            raise ValueError("Native contract prices changed")
        frame = pd.read_feather(native / filename)
        frame = frame.loc[frame.date.ge(start) & frame.date.lt(end)]
        closes[symbol] = frame.assign(date=frame.date.dt.as_unit("ns")).set_index("date").close
    results = context_features(panels, closes, raw_metrics)
    groups = expanded_groups(plan)
    coverage = []
    for symbol, panel in results.items():
        panel.to_parquet(output / f"{symbol}.parquet", index=False)
        coverage.append(
            {
                "symbol": symbol,
                "rows": len(panel),
                "finite_by_group": {
                    group: int(np.isfinite(panel[columns]).all(axis=1).sum())
                    for group, columns in groups.items()
                },
            }
        )
    (output / "labels").mkdir()
    for path in sorted((base / "labels").iterdir()):
        target = output / "labels" / path.name
        target.write_bytes(path.read_bytes())
        if sha(target) != sha(path):
            raise ValueError("Label copy mismatch")
    save(
        output / "result.json",
        {
            "coverage": coverage,
            "labels_unchanged": True,
            "reserved_test_labels_computed": False,
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    print(read(output / "result.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan.absolute(), args.output.absolute())
