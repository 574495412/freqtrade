"""Unchanged native experiment with a registered research-only custom-state index."""

import argparse
import json
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.indexed_custom_data import indexed_backtest_custom_data
from research.crypto.run_candidate import run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--phase",
        required=True,
        choices=["compatibility", "train", "validation", "holdout", "continuous"],
    )
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    sources = read(args.plan)["runtime_sources"]
    for source in sources:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Registered research runtime adapter changed")
    with indexed_backtest_custom_data():
        result = run(
            args.plan.absolute(), args.output.absolute(), args.candidate, args.phase, args.cost
        )
    save(
        args.output / "runtime_adapter.json",
        {
            "sources": sources,
            "backtest_custom_data_indexed": True,
            "database_mode_unchanged": True,
            "orders_enabled": False,
        },
    )
    print(json.dumps(result))
