"""Forward-only snapshot freshness gate; all financial rules remain inherited."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parents[2] / "nfi80_flatboost/strategy"):
    from NFIFlatTopBoostPaper import NFIFlatTopBoostPaper


def snapshot_pairs(path, now, policy_sha256):
    try:
        value = json.loads(Path(path).read_text())
        pairs = value["pairs"]
        created = datetime.fromisoformat(value["generated_at_utc"])
        expires = datetime.fromisoformat(value["valid_until_utc"])
        decision = datetime.fromisoformat(value["decision_day_utc"]).replace(tzinfo=UTC)
        input_close = datetime.fromisoformat(value["latest_input_close"])
        if (
            not isinstance(pairs, list)
            or len(pairs) != 80
            or any(not isinstance(p, str) or not p.endswith("/USDT:USDT") for p in pairs)
            or len(set(pairs)) != 80
            or created.tzinfo is None
            or expires.tzinfo is None
            or not created - timedelta(seconds=5) <= now < expires
            or not timedelta(0) < expires - created <= timedelta(days=1)
            or value["selection_returncode"] != 0
            or value["policy_sha256"] != policy_sha256
            or decision.date() != now.astimezone(UTC).date()
            or expires != decision + timedelta(days=1)
            or input_close.tzinfo is None
            or input_close >= decision
        ):
            return ()
        return tuple(pairs)
    except (OSError, ValueError, TypeError, KeyError):
        return ()


class DailySnapshotGate:
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "dry_run" or self.config.get("dry_run") is not True:
            raise ValueError("Daily snapshot experiment is forward paper-only, not a backtest")
        self.daily_snapshot_path = Path(self.config["daily_snapshot_file"])
        self.daily_policy_sha256 = self.config["daily_policy_sha256"]
        if not snapshot_pairs(
            self.daily_snapshot_path, datetime.now(UTC), self.daily_policy_sha256
        ):
            raise ValueError("A fresh, successful native selection snapshot is required")
        super().bot_start(**kwargs)

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
        pairs = snapshot_pairs(self.daily_snapshot_path, current_time, self.daily_policy_sha256)
        if pair not in pairs or set(self.dp.current_whitelist()) != set(pairs):
            return False
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )


class NFISelectedFlatBoost(DailySnapshotGate, NFIFlatTopBoostPaper):
    def version(self):
        return super().version() + "-daily"
