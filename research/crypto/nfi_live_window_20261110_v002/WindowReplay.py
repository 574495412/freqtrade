"""Backtest-only operational adapter; inherit every live financial callback."""

from pathlib import Path

from freqtrade.enums import RunMode
from freqtrade.resolvers.iresolver import PathModifier


ROOT = Path(__file__).resolve().parents[3]
with PathModifier(ROOT / "deploy/mainnet_100_20261109/strategy"):
    from NFIManualFlatBoost import NFIManualFlatBoost, NostalgiaForInfinityX8


class WindowReplay(NFIManualFlatBoost):
    def bot_start(self, **kwargs):
        if self.config["runmode"] != RunMode.BACKTEST or self.config.get("dry_run") is not True:
            raise ValueError("Window replay is exclusively a native historical backtest")
        self.paper_entry_start = None
        self.stake_audit = Path(self.config["user_data_dir"]) / "stake-decisions.jsonl"
        # Live-only startup admission is replaced, not trading decisions.
        # confirm_trade_entry, pool gate, allocation, exits, DCA and leverage stay inherited.
        NostalgiaForInfinityX8.bot_start(self, **kwargs)
