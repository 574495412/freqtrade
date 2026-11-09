"""Repeat one identical native account to audit storage parity, never select a new alpha."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi80_capacity_20261109.study import HERE, OUT, read, ref, save, sha


def financial_trades(folder):
    archives = list((folder / "native_results").glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("Native parity archive missing")
    with ZipFile(archives[0]) as archive:
        names = [
            n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        ]
        stats = next(iter(json.loads(archive.read(names[0]))["strategy"].values()))
    fields = (
        "pair",
        "is_short",
        "leverage",
        "amount",
        "open_rate",
        "close_rate",
        "open_timestamp",
        "close_timestamp",
        "fee_open",
        "fee_close",
        "funding_fees",
        "profit_abs",
        "exit_reason",
        "enter_tag",
    )
    order_fields = ("ft_is_entry", "amount", "safe_price", "order_filled_timestamp")
    trades = [
        {
            **{k: t.get(k) for k in fields},
            "orders": [{k: o.get(k) for k in order_fields} for o in t["orders"]],
        }
        for t in stats["trades"]
    ]
    return trades, ref(archives[0])


def main():
    protocol = read(HERE / "parity-protocol.json")
    for key in ("source", "plan", "baseline_result"):
        record = protocol[key]
        if sha(Path(record["path"])) != record["sha256"]:
            raise ValueError(f"Frozen parity {key} changed")
    baseline = OUT / "L3_S12_15bps"
    repeated = OUT / "parity_L3_S12_15bps_zstd"
    if not (repeated / "result.json").exists():
        if repeated.exists():
            raise FileExistsError("Prior partial parity attempt retained")
        with (OUT / "parity_L3_S12_15bps_zstd.log").open("x") as log:
            child = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(HERE / "native_runner.py"),
                    "--plan",
                    protocol["plan"]["path"],
                    "--candidate",
                    "L3_S12",
                    "--cost-bps",
                    "15",
                    "--output",
                    str(repeated),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        save(
            repeated / "completion.json",
            {"returncode": child.returncode, "purpose": "same-contract storage parity"},
        )
        if child.returncode:
            raise RuntimeError("Native parity run failed")
    if read(repeated / "completion.json")["returncode"] != 0:
        raise ValueError("Parity completion is not successful")
    original, original_ref = financial_trades(baseline)
    replayed, replayed_ref = financial_trades(repeated)
    left = pd.read_parquet(baseline / "five_minute_equity.parquet")
    right = pd.read_parquet(repeated / "five_minute_equity.parquet")
    same_clock = (
        pd.DatetimeIndex(left.date).as_unit("ns").equals(pd.DatetimeIndex(right.date).as_unit("ns"))
    )
    difference = (
        float(np.max(np.abs(left.marked_equity.to_numpy() - right.marked_equity.to_numpy())))
        if same_clock
        else None
    )
    passed = (
        same_clock
        and original == replayed
        and difference <= protocol["maximum_equity_difference_usdt"]
    )
    result = {
        "passed": passed,
        "financial_trades_exactly_equal": original == replayed,
        "same_observation_clocks": same_clock,
        "maximum_marked_equity_difference_usdt": difference,
        "original_archive": original_ref,
        "repeat_archive": replayed_ref,
        "original_curve": ref(baseline / "five_minute_equity.parquet"),
        "repeat_curve": ref(repeated / "five_minute_equity.parquet"),
        "protocol": ref(HERE / "parity-protocol.json"),
        "orders_enabled": False,
    }
    save(HERE / "native-storage-parity.json", result)
    if not passed:
        raise ValueError(
            "Storage parity failed; retain discrepancy, do not select a preferred result"
        )
    cache = repeated / "lossless_cache"
    if cache.exists() and not cache.is_symlink():
        shutil.rmtree(cache)
        save(repeated / "generated-cache-cleanup.json", {"successful_parity_cache_only": True})
    print(
        json.dumps(
            {"native_storage_parity_passed": passed, "maximum_equity_difference_usdt": difference}
        )
    )


if __name__ == "__main__":
    main()
