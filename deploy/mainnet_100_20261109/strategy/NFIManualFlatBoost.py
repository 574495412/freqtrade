"""User-started native NFI variant; finance callbacks inherit the pinned adapter."""

from datetime import UTC, datetime
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


ROOT = Path(__file__).resolve().parents[3]
with PathModifier(ROOT / "deploy/nfi80_flatboost/strategy"):
    from NFIFlatTopBoostPaper import NFIFlatTopBoostPaper, NostalgiaForInfinityX8
with PathModifier(ROOT / "deploy/nfi80_selected/strategy"):
    from NFISelectedPaper import snapshot_pairs


class NFIManualFlatBoost(NFIFlatTopBoostPaper):
    def bot_start(self, **kwargs):
        from deploy.mainnet_100_20261109.capital import CappedWallets

        if self.config.get("dry_run") is not False or self.config["runmode"].value != "live":
            raise ValueError("This adapter is for the user's explicit manual live invocation")
        if not isinstance(self.wallets, CappedWallets):
            raise ValueError("The native capital ceiling extension must be installed")
        self.paper_entry_start = None
        self.stake_audit = Path(self.config["user_data_dir"]) / "stake-decisions.jsonl"
        if self.config["trading_mode"].value == "futures" and not self._pool(datetime.now(UTC)):
            raise ValueError("A current closed-UTC daily pool is required")
        NostalgiaForInfinityX8.bot_start(self, **kwargs)

    def _pool(self, now):
        return snapshot_pairs(
            self.config["daily_snapshot_file"], now, self.config["daily_policy_sha256"]
        )

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if self.config["trading_mode"].value == "futures":
            pairs = self._pool(current_time)
            if pair not in pairs or set(self.dp.current_whitelist()) != set(pairs):
                return False
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )

    def version(self):
        return NostalgiaForInfinityX8.version(self) + "-manual-flatboost-cap100"
