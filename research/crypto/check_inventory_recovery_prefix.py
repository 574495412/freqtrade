"""Actual training-data prefix checks for the frozen recovery entry mechanism."""

import argparse
import sys
from pathlib import Path

import pandas as pd

from freqtrade.enums import CandleType
from freqtrade.research.data import read, save, sha
from freqtrade.strategy import merge_informative_pair


sys.path.insert(0, str(Path(__file__).resolve().parent / "strategies"))
from inventory_recovery_risk import InventoryRecovered


def check(plan_path, output):
    plan = read(plan_path)
    if output.exists():
        raise ValueError("Preserve previous prefix receipt")
    strategy = InventoryRecovered({"candle_type_def": CandleType.FUTURES})
    root = Path(plan["dataset"]) / "candles/futures"
    start = pd.Timestamp(plan["train_start"], tz="UTC")
    end = pd.Timestamp(plan["train_end"], tz="UTC")
    rows = []
    for coin in ["BTC", "ETH", "SOL"]:
        raw = {}
        for tf, warmup in [
            ("5m", pd.Timedelta(minutes=4000)),
            ("1h", pd.Timedelta(hours=800)),
            ("4h", pd.Timedelta(hours=3200)),
        ]:
            data = pd.read_feather(root / f"{coin}_USDT_USDT-{tf}-futures.feather")
            raw[tf] = data.loc[data.date.ge(start - warmup) & data.date.lt(end)].reset_index(
                drop=True
            )

        def signals(cut, raw=raw):
            frames = {tf: f.loc[f.date.lt(cut)].copy() for tf, f in raw.items()}
            frame = merge_informative_pair(
                frames["5m"], strategy.populate_hour(frames["1h"], {}), "5m", "1h", ffill=True
            )
            frame = merge_informative_pair(
                frame, strategy.populate_slow(frames["4h"], {}), "5m", "4h", ffill=True
            )
            return strategy.populate_entry_trend(frame, {})

        full = signals(end)
        for day in ["2024-03-01 03:55", "2024-06-01 11:55", "2024-09-01 19:55"]:
            cut = pd.Timestamp(day, tz="UTC")
            prefix = signals(cut)
            pd.testing.assert_frame_equal(
                full.loc[full.date.lt(cut)].reset_index(drop=True),
                prefix.reset_index(drop=True),
                check_exact=True,
                check_dtype=False,
            )
            rows.append(
                {
                    "coin": coin,
                    "cut_utc": cut.isoformat(),
                    "rows_compared": len(prefix),
                    "columns_compared": len(prefix.columns),
                    "passed": True,
                }
            )
    result = {
        "passed": len(rows) == 9,
        "cases": rows,
        "plan_sha256": sha(plan_path),
        "checker_source_sha256": sha(Path(__file__)),
        "orders_enabled": False,
        "scope": "Exact real-data prefix equality of native informative merges, indicators "
        "and recovery entry flags. It does not prove profitable execution.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, result)
    print({"real_prefix_cases": len(rows), "passed": result["passed"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    check(args.plan, args.output)
