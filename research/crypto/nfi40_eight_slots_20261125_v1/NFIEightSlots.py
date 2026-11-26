"""Historical capacity test with the original six-slot entry budget."""

from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class NFIEightSlots(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        runmode = self.config["runmode"]
        if (
            getattr(runmode, "value", runmode) != "backtest"
            or self.config["dry_run"] is not True
            or self.config["max_open_trades"] != 8
            or self.config["stake_amount"] != "unlimited"
        ):
            raise ValueError("Fixed historical eight-slot study only")
        super().bot_start(**kwargs)

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        # Reuse the native six-slot proposal, calculated on this account's
        # current wallet. The native validator applies actual venue/wallet limits.
        six_slot_proposal = self.wallets.get_trade_stake_amount(pair, 6, update=False)
        return super().custom_stake_amount(
            pair=pair, current_time=current_time, current_rate=current_rate,
            proposed_stake=six_slot_proposal, min_stake=min_stake,
            max_stake=max_stake, leverage=leverage, entry_tag=entry_tag, side=side,
            **kwargs,
        )
