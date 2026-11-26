"""Historical-only lifetime entry margin budget for pure short-rebuy trades."""

import json
from math import fsum
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier

with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import FlatTopBoost


class FlatBoostShortRebuyCap(FlatTopBoost):
    def bot_start(self, **kwargs):
        if (
            self.config["runmode"].value != "backtest"
            or self.config["dry_run"] is not True
            or self.config["max_open_trades"] != 12
        ):
            raise ValueError("Fixed historical short-rebuy cap only")
        super().bot_start(**kwargs)
        self.blocked_adjustments = Path(self.config["user_data_dir"]) / "blocked-short-rebuys.jsonl"

    def adjust_trade_position(self, trade, current_time, *args, **kwargs):
        proposed = super().adjust_trade_position(trade, current_time, *args, **kwargs)
        amount = proposed[0] if isinstance(proposed, tuple) else proposed
        tags = set((trade.enter_tag or "").split())
        if not trade.is_short or not tags or not tags.issubset(self.short_rebuy_mode_tags):
            return proposed
        if amount is None or amount <= 0:
            return proposed
        entries = trade.select_filled_orders(trade.entry_side)
        if not entries:
            raise ValueError("A funded rebuy requires a known first entry")
        initial_stake = entries[0].stake_amount_filled
        filled_stake = fsum(order.stake_amount_filled for order in entries)
        if filled_stake + amount <= 2 * initial_stake + 1e-9:
            return proposed
        with self.blocked_adjustments.open("a") as stream:
            stream.write(json.dumps({
                "pair": trade.pair, "time": current_time.isoformat(), "entry_tag": trade.enter_tag,
                "first_stake": initial_stake, "filled_entry_stake": filled_stake,
                "rejected_stake": amount,
            }) + "\n")
        return None
