"""Native account runner with causal precision selection and proposal-state observations."""

import argparse
import json
from pathlib import Path

import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import read, save, sha
from research.crypto import run_candidate
from research.crypto.causal_precision_account import CausalPrecisionBacktesting
from research.crypto.indexed_custom_data import indexed_backtest_custom_data


class RecordedPrecisionBacktesting(CausalPrecisionBacktesting):
    instances = []

    def __init__(self, config, dataset):
        self.precision_calls = []
        self.instances.append(self)
        super().__init__(config, dataset)

    def get_pair_precision(self, pair, current_time):
        tick, mode = super().get_pair_precision(pair, current_time)
        opened = Trade.get_trades_proxy(is_open=True)
        self.precision_calls.append(
            {
                "date": current_time,
                "pair": pair,
                "tick": tick,
                "mode": mode,
                "open_small": sum(t.enter_tag == "small_first_hit" for t in opened),
                "open_core": sum(t.enter_tag == "core_first_hit" for t in opened),
                "open_total": len(opened),
            }
        )
        return tick, mode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--phase", choices=["train", "validation", "holdout"], required=True)
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    sources = read(args.plan)["runtime_sources"]
    for source in sources:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Registered runtime changed")
    original = run_candidate.ObservedBacktesting
    try:
        run_candidate.ObservedBacktesting = RecordedPrecisionBacktesting
        with indexed_backtest_custom_data():
            result = run_candidate.run(
                args.plan.absolute(), args.output.absolute(), args.candidate, args.phase, args.cost
            )
    finally:
        run_candidate.ObservedBacktesting = original
    if len(RecordedPrecisionBacktesting.instances) != 1:
        raise ValueError("Unexpected native engine count")
    records = RecordedPrecisionBacktesting.instances[0].precision_calls
    columns = ["date", "pair", "tick", "mode", "open_small", "open_core", "open_total"]
    pd.DataFrame(records, columns=columns).to_parquet(
        args.output / "precision_calls.parquet", index=False
    )
    save(
        args.output / "runtime_adapter.json",
        {
            "sources": sources,
            "backtest_custom_data_indexed": True,
            "database_mode_unchanged": True,
            "price_precision_proxy": "already_closed_candles",
            "precision_calls_sha256": sha(args.output / "precision_calls.parquet"),
            "native_matching_source_changed": False,
            "orders_enabled": False,
        },
    )
    print(json.dumps(result))
