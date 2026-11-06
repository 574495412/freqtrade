"""Research-only extension of qualifying NFI X8 profit exits."""

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


PREFIX = "exit_profit_long_tc_"


class FlatProfitTail(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("Profit tail requires the historical backtest")
        super().bot_start(**kwargs)
        self.tails = {}
        self.tail_trace = Path(self.config["user_data_dir"]) / "tail-decisions.jsonl"
        self.hourly = {}

    def allow(self, pair, at):
        return True, None

    def trace(self, row):
        with self.tail_trace.open("a") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        native = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        tags = set((trade.enter_tag or "").split())
        if trade.is_short or not tags or not tags.issubset(self.long_top_coins_mode_tags):
            return native
        key = (pair, trade.open_date_utc)
        state = self.tails.get(key)
        profitable = isinstance(native, str) and native.startswith(PREFIX)
        stable_position = (
            trade.nr_of_successful_entries == 1
            and trade.nr_of_successful_exits == 0
            and not trade.has_open_orders
        )
        if state is None and (not profitable or current_profit < 0.005 or not stable_position):
            return native
        row = {
            "pair": pair,
            "opened": key[1].isoformat(),
            "time": current_time.isoformat(),
            "rate": current_rate,
            "profit_ratio": current_profit,
            "native": native,
            "unchanged_one_fill": stable_position,
            "tag": trade.enter_tag,
        }
        if state is None:
            allowed, available_hour = self.allow(pair, current_time)
            row.update(action="arm" if allowed else "decline", available_hour=available_hour)
            if allowed:
                self.tails[key] = {
                    "armed": current_time,
                    "peak": current_profit,
                    "allowance": current_profit / 2,
                    "release": None,
                }
            self.trace(row)
            return None if allowed else native

        state["peak"] = max(state["peak"], current_profit)
        if native and not profitable:
            state["release"] = native
        elif not state["release"]:
            if not stable_position:
                state["release"] = "tail_position_changed"
            elif current_profit <= state["peak"] - state["allowance"]:
                state["release"] = "tail_profit_drawback"
            elif current_time >= state["armed"] + timedelta(hours=24):
                state["release"] = "tail_24h_deadline"
        row.update(
            action="release" if state["release"] else "hold",
            exit=state["release"],
            peak=state["peak"],
            allowance=state["allowance"],
        )
        self.trace(row)
        return state["release"]


class TrendProfitTail(FlatProfitTail):
    def allow(self, pair, at):
        last = (pd.Timestamp(at).tz_convert("UTC") - pd.Timedelta(minutes=5)).floor(
            "h"
        ) - pd.Timedelta(hours=1)
        if pair not in self.hourly:
            s = pair.replace("/", "_").replace(":", "_")
            path = Path(self.config["datadir"]) / "futures" / f"{s}-1h-futures.feather"
            self.hourly[pair] = pd.read_feather(path).set_index("date").close
        clocks = pd.date_range(end=last, periods=25, freq="h")
        close = self.hourly[pair].reindex(clocks).to_numpy()
        qualified = np.isfinite(close).all() and (close > 0).all() and close[-1] > max(close[:-1])
        return bool(qualified), last.isoformat()
