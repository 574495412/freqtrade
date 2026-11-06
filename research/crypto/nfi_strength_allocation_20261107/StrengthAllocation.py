"""Research-only first-stake allocation on unmodified NFI X8 entries."""

import json
import math
from pathlib import Path

import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class StrengthAllocation(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("StrengthAllocation requires the historical backtest")
        super().bot_start(**kwargs)
        self.strength_prices = {}
        self.stake_audit = Path(self.config["user_data_dir"]) / "stake-decisions.jsonl"

    def daily_closes(self, pair):
        if pair not in self.strength_prices:
            frame = self.dp.historic_ohlcv(pair, "1d")
            self.strength_prices[pair] = frame.set_index("date").close
        return self.strength_prices[pair]

    def multiplier(self, pair, current_time):
        last_day = pd.Timestamp(current_time).floor("D") - pd.Timedelta(days=1)
        earlier = last_day - pd.Timedelta(days=30)
        asset = self.daily_closes(pair)
        btc = self.daily_closes("BTC/USDT:USDT")
        values = [asset.get(last_day), asset.get(earlier), btc.get(last_day), btc.get(earlier)]
        if any(value is None or not math.isfinite(value) or value <= 0 for value in values):
            return 1.0, None
        ratio = (values[0] / values[1]) / (values[2] / values[3])
        if ratio >= 1.05:
            return 1.75, ratio
        if ratio < 1.0:
            return 0.65, ratio
        return 1.0, ratio

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
        tags = set((entry_tag or "").split())
        if side != "long" or not tags or not tags.issubset(self.long_top_coins_mode_tags):
            return native
        multiplier, ratio = self.multiplier(pair, current_time)
        stake = min(max_stake, max(min_stake or 0.0, native * multiplier))
        with self.stake_audit.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "tag": entry_tag,
                        "relative_30d": ratio,
                        "multiplier": multiplier,
                        "native_stake": native,
                        "min_stake": min_stake,
                        "max_stake": max_stake,
                        "requested_stake": stake,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return stake


class FlatTopBoost(StrengthAllocation):
    def multiplier(self, pair, current_time):
        return 1.25, None
