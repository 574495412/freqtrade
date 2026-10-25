"""Run native sliced-signal diagnostics on the frozen selected strategy."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity


def check(run: Path):
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Unexpected configuration environment override")
    selection = read(run / "selection.json")
    chosen = selection["agent"]
    if not chosen or chosen == "cash":
        raise ValueError("No selected candidate")
    reference = run / "train" / chosen / "cost_3bps"
    if read(reference / "freeze.json")["source_identity"] != source_identity():
        raise ValueError("Native source identity changed")
    plan = read(run / "plan.json")
    for record in plan["strategy_sources"]:
        if sha(reference / "strategies" / Path(record["path"]).name) != record["sha256"]:
            raise ValueError("Frozen strategy source changed")
    folder = run / "bias"
    folder.mkdir(exist_ok=False)
    config = read(reference / "settings.json")
    config.update(user_data_dir=str(folder), exportdirectory=str(folder))
    save(folder / "settings.json", config)
    command = [
        sys.executable,
        "-m",
        "freqtrade",
        "lookahead-analysis",
        "--config",
        str(folder / "settings.json"),
        "--datadir",
        str(Path(plan["dataset"]) / "candles"),
        "--minimum-trade-amount",
        "10",
        "--targeted-trade-amount",
        "20",
        "--allow-limit-orders",
        "--lookahead-analysis-exportfilename",
        str(folder / "native.csv"),
    ]
    save(folder / "command.json", command)
    with (folder / "native.log").open("x") as log:
        result = subprocess.run(command, stdout=log, stderr=log, check=False)
    save(folder / "process.json", {"returncode": result.returncode})
    if result.returncode:
        raise RuntimeError("Native lookahead analysis failed; logs preserved")
    table = pd.read_csv(folder / "native.csv")
    if len(table) != 1 or table.iloc[0]["strategy"] != chosen:
        raise ValueError("Ambiguous native bias results")
    row = table.iloc[0]
    signals = int(row["total_signals"])
    passed = (
        str(row["has_bias"]).lower() == "false"
        and signals >= 20
        and int(row["biased_entry_signals"]) == 0
        and int(row["biased_exit_signals"]) == 0
    )
    summary = {
        "candidate": chosen,
        "native_lookahead_passed": passed,
        "signals": signals,
        "csv_sha256": sha(folder / "native.csv"),
        "scope": (
            "native sliced signals; limit orders preserved; analyzer forces 1B wallet, "
            "10k stake and unlimited slots; not profitability or whole-lifecycle equivalence"
        ),
        "orders_enabled": False,
    }
    save(run / "bias_result.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.run.resolve()), ensure_ascii=False, indent=2))
