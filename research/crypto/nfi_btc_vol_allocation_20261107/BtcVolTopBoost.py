"""Research overlay for BTC volatility conditioned X8 first stakes."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import StrengthAllocation


BTC_DAILY = "futures/BTC_USDT_USDT-1d-futures.feather"


class BtcVolTopBoost(StrengthAllocation):
    def bot_start(self, **kwargs):
        super().bot_start(**kwargs)
        # Read the admitted raw daily archive without Freqtrade gap filling.
        frame = pd.read_feather(Path(self.config["datadir"]) / BTC_DAILY)
        close = frame.set_index("date").close.asfreq("D")
        close = close.where(np.isfinite(close) & (close > 0))
        realized = np.sqrt(np.log(close).diff().pow(2).rolling(7, min_periods=7).sum())
        self.realized_vol = realized
        self.reference_vol = realized.shift(7).rolling(90, min_periods=90).median()
        self.vol_audit = Path(self.config["user_data_dir"]) / "btc-volatility.jsonl"

    def multiplier(self, pair, current_time):
        last_day = pd.Timestamp(current_time).tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        current, reference = (
            value if math.isfinite(value) and value > 0 else None
            for value in (
                self.realized_vol.get(last_day, math.nan),
                self.reference_vol.get(last_day, math.nan),
            )
        )
        boost = current is not None and reference is not None and current <= reference
        with self.vol_audit.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "last_closed_day": last_day.isoformat(),
                        "volatility": current,
                        "reference": reference,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return (1.75 if boost else 1.0), None
