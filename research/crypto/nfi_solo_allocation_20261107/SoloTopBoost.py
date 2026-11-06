"""NFI research overlay allocating more first stake to nonoverlapping longs."""

import json
from pathlib import Path

from freqtrade.persistence import Trade
from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import StrengthAllocation


class SoloTopBoost(StrengthAllocation):
    def multiplier(self, pair, current_time):
        longs = [
            {
                "pair": trade.pair,
                "opened": trade.open_date_utc.isoformat(),
                "quantity": trade.amount,
            }
            for trade in Trade.get_trades_proxy(is_open=True)
            if not trade.is_short
        ]
        path = Path(self.config["user_data_dir"]) / "long-inventory.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "longs": longs,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return (1.0 if longs else 1.5), None
