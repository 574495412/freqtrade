"""Reuse only frozen feature files; do not read labels or select on future returns."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.strategies.native_absorption import REQUIRED, events


def build(feature, price):
    frame = feature[["date", *[c for c in REQUIRED if c != "return5"]]].copy()
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError("Feature clock must be unique and increasing")
    if not frame.date.diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError("Feature clock has gaps")
    price = price.set_index("date").reindex(frame.date)
    if not np.isfinite(price.close).all():
        raise ValueError("Exact original feature prices required")
    frame["return5"] = np.log(price.close.to_numpy()) - np.log(price.close.shift(1).to_numpy())
    frame["feature_atr"] = frame.atr_fraction * price.close.to_numpy()
    frame = events(frame)
    frame["feature_open"] = frame.date
    # Feature candle t closes at t+5m; put signal on t+5m row for native entry at t+10m.
    frame["date"] = frame.date + pd.Timedelta(minutes=5)
    return frame


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-plan", type=Path, required=True)
    parser.add_argument("--context-panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous panel")
    plan, source = read(args.context_plan), args.context_panel
    native = Path(plan["base_dataset"])
    if sha(native / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Native input receipts changed")
    if read(source / "plan.json") != plan:
        raise ValueError("Context panel protocol mismatch")
    source_receipts, price_receipts = read(source / "receipts.json"), read(native / "receipts.json")
    args.output.mkdir(parents=True)
    coverage = []
    for symbol in plan["symbols"]:
        name = symbol + ".parquet"
        if sha(source / name) != source_receipts[name]:
            raise ValueError("Context features changed")
        filename = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        if sha(native / filename) != price_receipts[filename]:
            raise ValueError("Feature price changed")
        feature = pd.read_parquet(source / name)
        price = pd.read_feather(native / filename)
        panel = build(feature, price)
        panel.to_parquet(args.output / name, index=False)
        coverage.append({"symbol": symbol, "rows": len(panel)})
    save(
        args.output / "receipt.json",
        {
            "builder_sha256": sha(Path(__file__)),
            "rule_sha256": sha(Path(__file__).parent / "strategies/native_absorption.py"),
            "source_receipts_sha256": sha(source / "receipts.json"),
            "native_receipts_sha256": sha(native / "receipts.json"),
            "coverage": coverage,
            "panel_hashes": {s: sha(args.output / f"{s}.parquet") for s in plan["symbols"]},
            "future_label_files_read": False,
            "account_outcomes_computed": False,
        },
    )
    print({"symbols": len(coverage), "rows": sum(c["rows"] for c in coverage)})
