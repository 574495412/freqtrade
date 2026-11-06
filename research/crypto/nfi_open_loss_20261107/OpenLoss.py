"""Native X8 research overlay that reduces concentrated, observed open losses."""

import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


REASON = "same_side_open_loss"
LOSS_LIMIT = 0.05
MIN_POSITIONS = 3


class OpenLoss(NostalgiaForInfinityX8):
    def bot_start(self, **kwargs):
        if self.config["runmode"].value != "backtest":
            raise ValueError("The open-loss overlay is restricted to historical research")
        super().bot_start(**kwargs)
        self.closes = {}
        self.targets = set()
        self.clock = None
        self.output = Path(self.config["user_data_dir"])

    def closed_prices(self, pair):
        if pair not in self.closes:
            slug = pair.replace("/", "_").replace(":", "_")
            path = Path(self.config["datadir"]) / "futures" / f"{slug}-5m-futures.feather"
            df = pd.read_feather(path, columns=["date", "close"])
            if not df.date.is_monotonic_increasing or df.date.duplicated().any():
                raise ValueError(f"Invalid close series: {pair}")
            self.closes[pair] = df.set_index("date").close
        return self.closes[pair]

    def bot_loop_start(self, current_time, **kwargs):
        self.targets.clear()
        self.clock = current_time
        super().bot_loop_start(current_time, **kwargs)
        sides = defaultdict(list)
        for trade in Trade.get_trades_proxy(is_open=True):
            if trade.has_open_position:
                sides[trade.is_short].append(trade)
        prior = current_time - timedelta(minutes=5)
        for short, group in sides.items():
            if len(group) < MIN_POSITIONS or any(t.has_open_orders for t in group):
                continue
            values = []
            for trade in group:
                rate = self.closed_prices(trade.pair).get(prior, math.nan)
                if not math.isfinite(rate) or rate <= 0 or trade.leverage != 2.0:
                    break
                basis = trade.stake_amount * trade.leverage
                pnl = (trade.amount * rate - basis) * (-1 if short else 1)
                values.append(
                    {
                        "pair": trade.pair,
                        "trade_id": trade.id,
                        "quantity": trade.amount,
                        "open_ms": int(trade.open_date_utc.timestamp() * 1000),
                        "basis": basis,
                        "prior_close": float(rate),
                        "price_pnl": pnl,
                    }
                )
            if len(values) != len(group):
                self.log(
                    "unknown",
                    {
                        "time": current_time.isoformat(),
                        "prior_bar": prior.isoformat(),
                        "side": "short" if short else "long",
                        "pairs": [t.pair for t in group],
                    },
                )
                continue
            basis = math.fsum(row["basis"] for row in values)
            loss = -math.fsum(row["price_pnl"] for row in values) / basis
            if loss < LOSS_LIMIT:
                continue
            worst = min(values, key=lambda row: (row["price_pnl"], row["pair"]))
            self.targets.add(worst["trade_id"])
            self.log(
                "trigger",
                {
                    "time": current_time.isoformat(),
                    "prior_bar": prior.isoformat(),
                    "side": "short" if short else "long",
                    "holdings": values,
                    "loss_ratio": loss,
                    "target": worst["pair"],
                    "target_id": worst["trade_id"],
                    "target_open_ms": worst["open_ms"],
                },
            )

    def log(self, kind, row):
        with (self.output / f"open_loss_{kind}.jsonl").open("a") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")

    def selected(self, trade, current_time):
        return self.clock == current_time and trade.id in self.targets

    def adjust_trade_position(self, trade, current_time, **kwargs):
        native = super().adjust_trade_position(trade, current_time, **kwargs)
        value = native[0] if isinstance(native, tuple) else native
        if self.selected(trade, current_time) and value is not None and value > 0:
            return None
        return native

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        native = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if native:
            return native
        if self.selected(trade, current_time) and not trade.has_open_orders:
            return REASON
        return native
