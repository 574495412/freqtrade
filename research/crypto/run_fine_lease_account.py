"""Native minute-detail matching with an explicit no-observed-trade execution gate."""

import argparse
import json
from pathlib import Path

import pandas as pd

from freqtrade.persistence import LocalTrade
from freqtrade.research.data import read, save, sha
from research.crypto import run_candidate
from research.crypto.indexed_custom_data import indexed_backtest_custom_data
from research.crypto.run_ranked_first_hit_account import CachedRankedBacktesting


class GatedMinuteBacktesting(CachedRankedBacktesting):
    instances = []

    def __init__(self, config, dataset):
        self.detail_valid, self.unpriced_steps = {}, []
        record = config["research_detail_manifest"]
        if sha(Path(record["path"])) != record["sha256"]:
            raise ValueError("Detail source manifest changed")
        manifest = read(Path(record["path"]))
        for pair in config["exchange"]["pair_whitelist"]:
            source = manifest["masks"][pair.split("/")[0] + "USDT"]
            if sha(Path(source["path"])) != source["sha256"]:
                raise ValueError("No-trade mask changed")
            self.detail_valid[pair] = (
                pd.read_parquet(source["path"]).set_index("date").has_observed_trade
            )
        super().__init__(config, dataset)

    def backtest_loop(self, row, pair, current_time, trade_dir, can_enter):
        known = self.detail_valid[pair].get(pd.Timestamp(current_time))
        if known is None:
            raise ValueError("No source clock for detail execution")
        if not known:
            opened = LocalTrade.bt_trades_open_pp[pair]
            if opened or trade_dir is not None:
                self.unpriced_steps.append(
                    {
                        "clock": pd.Timestamp(current_time).isoformat(),
                        "pair": pair,
                        "open_positions": len(opened),
                        "entry_intent": trade_dir,
                    }
                )
            return None
        return super().backtest_loop(row, pair, current_time, trade_dir, can_enter)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--phase", choices=["train"], required=True)
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    for record in plan["runtime_sources"]:
        if sha(Path(record["path"])) != record["sha256"]:
            raise ValueError("Frozen runtime changed")
    original = run_candidate.ObservedBacktesting
    try:
        run_candidate.ObservedBacktesting = GatedMinuteBacktesting
        with indexed_backtest_custom_data():
            result = run_candidate.run(
                args.plan.absolute(), args.output.absolute(), args.candidate, args.phase, args.cost
            )
    finally:
        run_candidate.ObservedBacktesting = original
    if len(GatedMinuteBacktesting.instances) != 1:
        raise ValueError("Unexpected native engine count")
    engine = GatedMinuteBacktesting.instances[0]
    pd.DataFrame(engine.precision_calls).to_parquet(
        args.output / "precision_calls.parquet", index=False
    )
    save(args.output / "ranking_decisions.json", getattr(engine.strategy, "_rank_decisions", []))
    save(args.output / "unpriced_execution_steps.json", engine.unpriced_steps)
    save(
        args.output / "runtime_adapter.json",
        {
            "sources": plan["runtime_sources"],
            "price_precision_proxy": "already_closed_candles",
            "execution_convention": "native1m-detail-positive-observed-trade-gate-v1",
            "research_execution_gate_applied": True,
            "native_matching_source_changed": False,
            "database_mode_unchanged": True,
            "backtest_custom_data_indexed": True,
            "precision_calls_sha256": sha(args.output / "precision_calls.parquet"),
            "ranking_decisions_sha256": sha(args.output / "ranking_decisions.json"),
            "unpriced_steps_sha256": sha(args.output / "unpriced_execution_steps.json"),
            "orders_enabled": False,
        },
    )
    print(json.dumps(result))
