"""Native account with exact cached causal precision and recorded ranking decisions."""

import argparse
import json
from pathlib import Path

import pandas as pd

from freqtrade.configuration import TimeRange
from freqtrade.persistence import Trade
from freqtrade.research.data import read, save, sha
from research.crypto import run_candidate
from research.crypto.indexed_custom_data import indexed_backtest_custom_data
from research.crypto.run_first_hit_account import RecordedPrecisionBacktesting
from research.crypto.run_nfi import ObservedBacktesting


class CachedRankedBacktesting(RecordedPrecisionBacktesting):
    instances = []

    def __init__(self, config, dataset):
        self.precision_calls = []
        self.instances.append(self)
        self.closed_precision = {}
        record = config["research_precision_cache"]
        if sha(Path(record["path"])) != record["sha256"]:
            raise ValueError("Precision cache changed")
        manifest = read(Path(record["path"]))
        timerange = TimeRange.parse_timerange(config["timerange"])
        if timerange.startdt < pd.Timestamp(
            manifest["start"], tz="UTC"
        ) or timerange.stopdt >= pd.Timestamp(manifest["end"], tz="UTC"):
            raise ValueError("Backtest exceeds cached causal precision coverage")
        for pair in config["exchange"]["pair_whitelist"]:
            source = manifest["files"][pair.split("/")[0] + "USDT"]
            path = Path(source["path"])
            if sha(path) != source["sha256"]:
                raise ValueError("Cached precision series changed")
            self.closed_precision[pair] = pd.read_parquet(path).known_tick
        ObservedBacktesting.__init__(self, config, dataset)

    def get_pair_precision(self, pair, current_time):
        result = super().get_pair_precision(pair, current_time)
        self.precision_calls[-1]["open_pairs"] = sorted(
            t.pair for t in Trade.get_trades_proxy(is_open=True)
        )
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["plan", "output"]:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--phase", choices=["train"], required=True)
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    for source in plan["runtime_sources"]:
        if sha(Path(source["path"])) != source["sha256"]:
            raise ValueError("Frozen runtime changed")
    original = run_candidate.ObservedBacktesting
    try:
        run_candidate.ObservedBacktesting = CachedRankedBacktesting
        with indexed_backtest_custom_data():
            result = run_candidate.run(
                args.plan.absolute(), args.output.absolute(), args.candidate, args.phase, args.cost
            )
    finally:
        run_candidate.ObservedBacktesting = original
    if len(CachedRankedBacktesting.instances) != 1:
        raise ValueError("Unexpected native engine count")
    engine = CachedRankedBacktesting.instances[0]
    columns = [
        "date",
        "pair",
        "tick",
        "mode",
        "open_small",
        "open_core",
        "open_total",
        "open_pairs",
    ]
    pd.DataFrame(engine.precision_calls, columns=columns).to_parquet(
        args.output / "precision_calls.parquet", index=False
    )
    save(args.output / "ranking_decisions.json", getattr(engine.strategy, "_rank_decisions", []))
    save(
        args.output / "runtime_adapter.json",
        {
            "sources": plan["runtime_sources"],
            "price_precision_proxy": "already_closed_candles",
            "database_mode_unchanged": True,
            "backtest_custom_data_indexed": True,
            "precision_calls_sha256": sha(args.output / "precision_calls.parquet"),
            "ranking_decisions_sha256": sha(args.output / "ranking_decisions.json"),
            "precision_cache_manifest_sha256": plan["settings"]["research_precision_cache"][
                "sha256"
            ],
            "native_matching_source_changed": False,
            "orders_enabled": False,
        },
    )
    print(json.dumps(result))
