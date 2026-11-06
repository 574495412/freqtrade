"""Research-only BTC trend confirmation for otherwise native X8 entries."""

import json
import math
from pathlib import Path

import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class BtcDirection(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("BTC direction gate is a historical experiment")
        super().bot_start(**kwargs)
        path = Path(self.config["datadir"]) / "futures/BTC_USDT_USDT-1d-futures.feather"
        self.btc = pd.read_feather(path).set_index("date").close
        self.admission = Path(self.config["user_data_dir"]) / "btc-direction.jsonl"

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        permitted = super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )
        if not permitted:
            return False
        cutoff = pd.Timestamp(current_time).tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        dates = pd.date_range(end=cutoff, periods=30, freq="D")
        close = self.btc.reindex(dates).tolist()
        mean = None
        if all(math.isfinite(p) and p > 0 for p in close):
            mean = math.fsum(close) / 30
        accepted = mean is not None and (
            (side == "long" and close[-1] >= mean) or (side == "short" and close[-1] < mean)
        )
        with self.admission.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "pair": pair,
                        "side": side,
                        "tag": entry_tag,
                        "time": current_time.isoformat(),
                        "last_daily": cutoff.isoformat(),
                        "close": close[-1] if mean is not None else None,
                        "sma_30": mean,
                        "accepted": accepted,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return accepted
