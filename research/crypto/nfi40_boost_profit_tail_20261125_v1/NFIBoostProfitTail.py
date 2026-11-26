"""Compose the frozen Top Coins stake and delayed-exit policies for research."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier

with PathModifier(Path(__file__).resolve().parent):
    from ProfitTail import FlatProfitTail
    from StrengthAllocation import FlatTopBoost


class NFIBoostProfitTail(FlatProfitTail, FlatTopBoost):
    def bot_start(self, **kwargs):
        if (
            self.config["runmode"].value != "backtest"
            or self.config["dry_run"] is not True
            or self.config["max_open_trades"] != 6
        ):
            raise ValueError("Fixed historical six-slot composition only")
        super().bot_start(**kwargs)
