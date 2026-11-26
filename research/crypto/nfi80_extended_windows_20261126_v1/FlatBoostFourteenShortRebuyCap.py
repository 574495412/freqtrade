"""Compose the pinned fourteen-slot sizing rule with the pinned short-rebuy cap."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from FlatBoostFourteenSlots import FlatBoostFourteenSlots
    from FlatBoostShortRebuyCap import FlatBoostShortRebuyCap
    from StrengthAllocation import FlatTopBoost


class FlatBoostFourteenShortRebuyCap(FlatBoostFourteenSlots, FlatBoostShortRebuyCap):
    def bot_start(self, **kwargs):
        if (
            self.config["runmode"].value != "backtest"
            or self.config["dry_run"] is not True
            or self.config["max_open_trades"] != 14
            or self.config["stake_amount"] != "unlimited"
        ):
            raise ValueError("Fixed historical fourteen-slot short-rebuy cap only")
        # Both parent startup guards describe their original, incompatible slot counts.
        FlatTopBoost.bot_start(self, **kwargs)
        self.blocked_adjustments = Path(self.config["user_data_dir"]) / "blocked-short-rebuys.jsonl"
