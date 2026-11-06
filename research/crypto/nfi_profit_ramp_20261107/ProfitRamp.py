"""Research-only realized-profit conditioning of native Top Coins first stakes."""

import json
import math
from pathlib import Path

from freqtrade.persistence import Trade
from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import StrengthAllocation


class ProfitRamp(StrengthAllocation):
    def multiplier(self, pair, current_time):
        closed = [
            t.close_profit_abs
            for t in Trade.get_trades_proxy(is_open=False)
            if t.close_date_utc is not None and t.close_date_utc < current_time
        ]
        if any(p is None or not math.isfinite(p) for p in closed):
            raise ValueError("Incomplete realized native profit")
        realized = math.fsum(closed)
        ratio = 1.25 + min(0.50, max(0.0, realized) / 500)
        path = Path(self.config["user_data_dir"]) / "realized-profit.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "strictly_prior_closes": len(closed),
                        "realized_usdt": realized,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return ratio, None
