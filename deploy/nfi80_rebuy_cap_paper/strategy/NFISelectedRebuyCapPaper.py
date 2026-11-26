"""Apply the historical 14-slot candidate to the independent daily-pool paper worker."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parents[2] / "nfi80_selected/strategy"):
    from NFISelectedPaper import NFISelectedFlatBoost
with PathModifier(Path(__file__).resolve().parent):
    from nfi_short_rebuy_capacity import FourteenSlotShortRebuyCap


class NFISelectedRebuyCapPaper(FourteenSlotShortRebuyCap, NFISelectedFlatBoost):
    def bot_start(self, **kwargs):
        if (
            self.config.get("dry_run") is not True
            or self.config["runmode"].value != "dry_run"
            or self.config["trading_mode"].value != "futures"
            or self.config["margin_mode"] != "isolated"
            or self.config["max_open_trades"] != 14
            or self.config["stake_amount"] != "unlimited"
        ):
            raise ValueError("Expected the isolated 14-slot futures paper account")
        super().bot_start(**kwargs)

    def version(self):
        return super().version() + "-14slots-short-rebuy-cap"
