"""Research-only matched short-rebuy first-stake controls."""

import json
from bisect import bisect_right
from decimal import Decimal
from hashlib import sha256
from itertools import pairwise
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


HOUR_MS = 3_600_000


class FlatRebuyBoost(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("Short-rebuy experiment requires historical backtest")
        super().bot_start(**kwargs)
        self.sizing_path = Path(self.config["user_data_dir"]) / "short-stakes.jsonl"

    def funding_signal(self, pair, now):
        return True, None, None

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
        if side != "short" or not tags or not tags.issubset(self.short_rebuy_mode_tags):
            return native
        eligible, rates, cutoff = self.funding_signal(pair, current_time)
        multiplier = 2.5 if eligible else 1.0
        requested = min(max_stake, max(min_stake or 0, native * multiplier))
        with self.sizing_path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "pair": pair,
                        "time": current_time.isoformat(),
                        "tag": entry_tag,
                        "native_stake": native,
                        "requested_stake": requested,
                        "min_stake": min_stake,
                        "max_stake": max_stake,
                        "multiplier": multiplier,
                        "settled_rates": rates,
                        "cutoff_ms": cutoff,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return requested


class FundingRebuyBoost(FlatRebuyBoost):
    def bot_start(self, **kwargs):
        super().bot_start(**kwargs)
        receipt = self.config["research_funding_signal"]
        raw = Path(receipt["path"]).read_bytes()
        if sha256(raw).hexdigest() != receipt["sha256"]:
            raise ValueError("Funding signal archive changed")
        self.settlements = json.loads(raw)

    def funding_signal(self, pair, now):
        cutoff = int(now.timestamp() * 1000) - 900_000
        start = cutoff - 24 * HOUR_MS
        clocks, rates = self.settlements[pair]
        i, j = bisect_right(clocks, start), bisect_right(clocks, cutoff)
        ts, settled = clocks[i:j], rates[i:j]
        gaps = [start, *ts, cutoff]
        complete = len(settled) >= 3 and max(b - a for a, b in pairwise(gaps)) <= 9 * HOUR_MS
        boost = (
            complete
            and settled[-1] > 0
            and sum(Decimal(str(r)) for r in settled) >= Decimal("0.0003")
        )
        return boost, [[t, r] for t, r in zip(ts, settled, strict=True)], cutoff
