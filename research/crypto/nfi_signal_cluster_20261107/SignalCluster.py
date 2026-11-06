"""Historical-only X8 sizing from the full previous-bar signal cohort."""

import json
import math
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class SignalCluster(NostalgiaForInfinityX8):
    flat = False

    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("Signal cluster experiment requires backtesting")
        super().bot_start(**kwargs)
        self.breadth = {"long": Counter(), "short": Counter()}
        self.signal_files = set()
        self.out = Path(self.config["user_data_dir"])
        (self.out / "signals").mkdir()

    def populate_exit_trend(self, df, metadata):
        df = super().populate_exit_trend(df, metadata)
        pair = metadata["pair"]
        if pair in self.signal_files or df.date.duplicated().any():
            raise ValueError(f"Repeated analyzed signal feed: {pair}")
        self.signal_files.add(pair)
        long = df.enter_long.eq(1)
        short = df.enter_short.eq(1)
        rows = []
        for side, mask in (
            ("long", long & ~short & ~df.exit_long.eq(1)),
            ("short", short & ~long & ~df.exit_short.eq(1)),
        ):
            signal = df.loc[mask, ["date", "enter_tag"]].copy()
            signal["side"] = side
            self.breadth[side].update(signal.date.dt.as_unit("ns").astype("int64") // 1000000)
            rows.append(signal)
        name = pair.replace("/", "_").replace(":", "_")
        pd.concat(rows).sort_values("date").to_parquet(self.out / "signals" / f"{name}.parquet")
        return df

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        native = super().custom_stake_amount(
            pair,
            current_time,
            current_rate,
            proposed_stake,
            min_stake,
            max_stake,
            leverage,
            entry_tag,
            side,
            **kwargs,
        )
        signal_time = current_time - timedelta(minutes=5)
        clock = pd.Timestamp(signal_time).value // 1000000
        count = self.breadth[side][clock]
        complete = self.signal_files == set(self.config["exchange"]["pair_whitelist"])
        factor = 0.75 if self.flat else max(0.5, 1 / math.sqrt(max(count, 1)))
        stake = min(max_stake, native * factor)
        if not complete or count < 1 or leverage != 2 or stake < (min_stake or 0):
            stake = 0.0
        row = {
            "pair": pair,
            "decision_time": current_time.isoformat(),
            "signal_time": signal_time.isoformat(),
            "side": side,
            "tag": entry_tag,
            "count": count,
            "native_stake": native,
            "factor": factor,
            "min_stake": min_stake,
            "max_stake": max_stake,
            "requested_stake": stake,
            "complete": complete,
            "leverage": leverage,
        }
        with (self.out / "stake_decisions.jsonl").open("a") as f:
            f.write(json.dumps(row, allow_nan=False) + "\n")
        return stake


class Flat75(SignalCluster):
    flat = True
