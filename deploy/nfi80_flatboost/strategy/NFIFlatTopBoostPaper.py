"""Paper-only operational guard and unchanged fixed x1.25 initial-stake rule."""

import json
from datetime import datetime
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


base_directory = Path(__file__).resolve().parent
if not (base_directory / "NostalgiaForInfinityX8.py").is_file():
    base_directory = Path(__file__).resolve().parents[2] / "strategy/nfi-x8"
with PathModifier(base_directory):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class NFIX8PaperControl(NostalgiaForInfinityX8):
    """Original financial rules; reject live mode and permit a common paper start."""

    def bot_start(self, **kwargs):
        mode = self.config["runmode"].value
        if self.config.get("dry_run") is not True or mode not in ("dry_run", "backtest"):
            raise ValueError("This release permits dry-run and historical parity only")
        self.paper_entry_start = None
        if mode == "dry_run":
            self.paper_entry_start = datetime.fromisoformat(self.config["paper_entry_start"])
            if self.paper_entry_start.tzinfo is None:
                raise ValueError("Paper start must have an explicit timezone")
        super().bot_start(**kwargs)
        self.stake_audit = Path(self.config["user_data_dir"]) / "stake-decisions.jsonl"

    def confirm_trade_entry(
        self,
        pair,
        order_type,
        amount,
        rate,
        time_in_force,
        current_time,
        entry_tag,
        side,
        **kwargs,
    ):
        if self.paper_entry_start is not None and current_time < self.paper_entry_start:
            return False
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )

    def version(self):
        return super().version() + "-paper-control"


class NFIFlatTopBoostPaper(NFIX8PaperControl):
    """Same financial allocation as research FlatTopBoost, with the paper guard."""

    def version(self):
        return NostalgiaForInfinityX8.version(self) + "-flatboost1.25-paper"

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
        multiplier = 1.25
        stake = min(max_stake, max(min_stake or 0.0, native * multiplier))
        with self.stake_audit.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "tag": entry_tag,
                        "relative_30d": None,
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
