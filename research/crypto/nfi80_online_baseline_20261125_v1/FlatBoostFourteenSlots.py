"""Historical capacity test under the daily 80-pair, 3x account contract."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier

with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import FlatTopBoost


class FlatBoostFourteenSlots(FlatTopBoost):
    def bot_start(self, **kwargs):
        if (
            self.config["runmode"].value != "backtest"
            or self.config["dry_run"] is not True
            or self.config["max_open_trades"] != 14
            or self.config["stake_amount"] != "unlimited"
        ):
            raise ValueError("Fixed historical fourteen-slot account only")
        super().bot_start(**kwargs)

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        twelve_slot_proposal = self.wallets.get_trade_stake_amount(pair, 12, update=False)
        return super().custom_stake_amount(
            pair, current_time, current_rate, twelve_slot_proposal, min_stake,
            max_stake, leverage, entry_tag, side, **kwargs,
        )
