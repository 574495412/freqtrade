"""Backtest-only reuse of the frozen entry-tag score for initial stake sizing."""

import json
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier

with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class NFITagPayoffBoost(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if (self.config["runmode"].value != "backtest" or self.config["dry_run"] is not True
                or self.config["max_open_trades"] != 12):
            raise ValueError("NFITagPayoffBoost requires the isolated historical test")
        super().bot_start(**kwargs)
        policy = json.loads(Path(__file__).with_name("tag_payoff_model.json").read_text())
        self.eligible_tags = frozenset(policy["eligible_tags"])
        self.stake_audit = Path(self.config["user_data_dir"]) / "tag-payoff-stake-decisions.jsonl"

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        native = super().custom_stake_amount(
            pair, current_time, current_rate, proposed_stake, min_stake,
            max_stake, leverage, entry_tag, side, **kwargs,
        )
        tag = ("-1:" if side == "short" else "1:") + ",".join(sorted((entry_tag or "").split()))
        eligible = tag in self.eligible_tags
        requested = min(max_stake, max(min_stake or 0.0, native * 1.25)) if eligible else native
        with self.stake_audit.open("a") as stream:
            stream.write(json.dumps({
                "pair": pair, "clock": current_time.isoformat(), "tag": tag, "eligible": eligible,
                "native": native, "minimum": min_stake, "maximum": max_stake,
                "requested": requested,
            }, allow_nan=False) + "\n")
        return requested
