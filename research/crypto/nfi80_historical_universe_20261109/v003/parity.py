"""Require complete exact old-account parity for the consumed-cache adapter."""

import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from research.crypto.nfi80_historical_universe_20261109.prepare import ROOT, ref, save


def validate(folder):
    old = ROOT / "user_data/crypto_quant/nfi80_flatboost_20261109_v001/L3_S12_15bps"
    columns = ["date", "equity", "marked_equity", "closed_realized_pnl", "open_trades", "gross"]
    before = pd.read_parquet(old / "five_minute_equity.parquet")
    after = pd.read_parquet(folder / "five_minute_equity.parquet")
    pd.testing.assert_frame_equal(before[columns], after[columns], check_exact=True)

    def trades(path):
        with ZipFile(next((path / "native_results").glob("*.zip"))) as z:
            name = next(
                n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")
            )
            return json.loads(z.read(name))["strategy"]["FlatTopBoost"]["trades"]

    left, right = trades(old), trades(folder)
    if left != right:
        raise ValueError("Consumed-cache native trade records differ")
    save(
        Path(__file__).resolve().parent / "parity-result.json",
        {
            "passed": True,
            "exact_equity_rows": len(before),
            "exact_native_trade_records": len(left),
            "maximum_equity_difference": 0,
            "new_result": ref(folder / "result.json"),
            "old_result": ref(old / "result.json"),
        },
    )
