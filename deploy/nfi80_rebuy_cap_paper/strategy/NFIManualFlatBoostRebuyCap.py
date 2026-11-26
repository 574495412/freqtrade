"""Futures candidate for the existing NFI live worker and account contract."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


HERE = Path(__file__).resolve().parent
with PathModifier(HERE.parents[1] / "mainnet_100_20261109/strategy"):
    from NFIManualFlatBoost import NFIManualFlatBoost
with PathModifier(HERE):
    from nfi_short_rebuy_capacity import FourteenSlotShortRebuyCap


class NFIManualFlatBoostRebuyCap(FourteenSlotShortRebuyCap, NFIManualFlatBoost):
    def bot_start(self, **kwargs):
        if (
            self.config.get("dry_run") is not False
            or self.config["runmode"].value != "live"
            or self.config["trading_mode"].value != "futures"
            or self.config["margin_mode"] != "isolated"
            or self.config["max_open_trades"] != 14
            or self.config["stake_amount"] != "unlimited"
        ):
            raise ValueError("Expected the 14-slot isolated futures live account contract")
        super().bot_start(**kwargs)

    def version(self):
        return super().version() + "-14slots-short-rebuy-cap"
