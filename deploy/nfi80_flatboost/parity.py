"""One native full-window replay verifies the paper adapter's financial parity."""

import argparse
import json
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

from research.crypto.nfi80_capacity_20261109.parity import financial_trades
from research.crypto.nfi80_capacity_20261109.study import ROOT, read, ref, save, sha


HERE = ROOT / "deploy/nfi80_flatboost"
PARENT = ROOT / "research/crypto/nfi80_flatboost_20261109"
OUT = ROOT / "user_data/crypto_quant/nfi80_flatboost_paper_parity_20261109_v001"
RUNNER = ROOT / "research/crypto/nfi80_capacity_20261109/native_runner.py"


def prepare():
    if (HERE / "parity-plan.json").exists() or OUT.exists():
        raise FileExistsError("Preserve prior parity registration")
    OUT.mkdir(parents=True)
    plan = read(PARENT / "L3_S12-plan.json")
    config = read(PARENT / "L3_S12-config.json")
    config["strategy"] = "NFIFlatTopBoostPaper"
    save(HERE / "parity-config.json", config)
    candidate = plan["candidates"][0]
    candidate.update(strategy="NFIFlatTopBoostPaper", config=ref(HERE / "parity-config.json"))
    candidate["source_files"].pop("StrengthAllocation.py")
    candidate["source_files"]["NFIFlatTopBoostPaper.py"] = ref(
        HERE / "strategy/NFIFlatTopBoostPaper.py"
    )
    plan["paper_port_parity_only"] = True
    plan["expected_financial_contract_unchanged"] = True
    save(HERE / "parity-plan.json", plan)
    save(
        HERE / "parity-registration.json",
        {
            "source": ref(__file__),
            "plan": ref(HERE / "parity-plan.json"),
            "baseline": str(
                ROOT / "user_data/crypto_quant/nfi80_flatboost_20261109_v001/L3_S12_8bps"
            ),
            "cost_bps": 8,
            "maximum_equity_difference_usdt": 1e-8,
            "orders_enabled": False,
            "purpose": "Paper source port, not new parameter selection",
        },
    )


def run():
    protocol = read(HERE / "parity-registration.json")
    if sha(__file__) != protocol["source"]["sha256"]:
        raise ValueError("Parity source changed")
    if sha(HERE / "parity-plan.json") != protocol["plan"]["sha256"]:
        raise ValueError("Parity plan changed")
    folder = OUT / "account"
    if folder.exists():
        raise FileExistsError("Preserve existing account")
    with (OUT / "native.log").open("x") as log:
        child = subprocess.run(
            [
                sys.executable,
                "-B",
                str(RUNNER),
                "--plan",
                protocol["plan"]["path"],
                "--candidate",
                "L3_S12",
                "--cost-bps",
                "8",
                "--output",
                str(folder),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    save(folder / "completion.json", {"returncode": child.returncode, "orders_enabled": False})
    if child.returncode:
        raise RuntimeError("Native paper parity replay failed")
    from pathlib import Path

    baseline = Path(protocol["baseline"])
    original, original_archive = financial_trades(baseline)
    actual, actual_archive = financial_trades(folder)
    left = pd.read_parquet(baseline / "five_minute_equity.parquet")
    right = pd.read_parquet(folder / "five_minute_equity.parquet")
    same_clock = (
        pd.DatetimeIndex(left.date).as_unit("ns").equals(pd.DatetimeIndex(right.date).as_unit("ns"))
    )
    error = float(np.max(np.abs(left.marked_equity.to_numpy() - right.marked_equity.to_numpy())))
    passed = same_clock and original == actual and error <= 1e-8
    save(
        HERE / "parity-result.json",
        {
            "passed": passed,
            "financial_trades_exactly_equal": original == actual,
            "same_observation_clocks": same_clock,
            "maximum_marked_equity_difference_usdt": error,
            "original_archive": original_archive,
            "actual_archive": actual_archive,
            "curve": ref(folder / "five_minute_equity.parquet"),
            "completion": ref(folder / "completion.json"),
            "orders_enabled": False,
        },
    )
    if not passed:
        raise ValueError("Paper adapter differs financially; deployment blocked")
    shutil.rmtree(folder / "lossless_cache")
    print(json.dumps({"paper_adapter_native_parity": passed, "maximum_equity_error": error}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    globals()[parser.parse_args().action]()
