"""Native X8 research sizing with a lagged same-side overlap score."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import StrengthAllocation


class CorrelationTopBoost(StrengthAllocation):
    def bot_start(self, **kwargs):
        super().bot_start(**kwargs)
        self.pair_closes = {}

    def closes(self, pair):
        if pair not in self.pair_closes:
            stem = pair.replace("/", "_").replace(":", "_")
            source = Path(self.config["datadir"]) / "futures" / f"{stem}-1d-futures.feather"
            close = pd.read_feather(source).set_index("date").close.asfreq("D")
            self.pair_closes[pair] = close.where(np.isfinite(close) & (close > 0))
        return self.pair_closes[pair]

    def multiplier(self, pair, current_time):
        cutoff = pd.Timestamp(current_time).tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        dates = pd.date_range(end=cutoff, periods=31, freq="D")
        current_returns = np.diff(np.log(self.closes(pair).reindex(dates).to_numpy()))
        eligible = np.isfinite(current_returns).all() and current_returns.std() > 0
        realized = math.fsum(
            t.close_profit_abs
            for t in Trade.get_trades_proxy(is_open=False)
            if t.close_date_utc is not None and t.close_date_utc < current_time
        )
        capital = self.config["dry_run_wallet"] + realized
        holdings = []
        for trade in Trade.get_trades_proxy(is_open=True):
            if trade.is_short:
                continue
            quantity = trade.amount if trade.nr_of_successful_entries else 0.0
            pending = math.fsum(
                order.safe_remaining * order.safe_placement_price
                for order in trade.open_orders
                if order.ft_order_side == trade.entry_side
            )
            if not quantity and not pending:
                continue
            close = self.closes(trade.pair).reindex(dates).to_numpy()
            prior = np.diff(np.log(close))
            rho = (
                float(np.clip(np.corrcoef(current_returns, prior)[0, 1], -1, 1))
                if eligible and np.isfinite(prior).all() and prior.std() > 0
                else None
            )
            holdings.append(
                {
                    "pair": trade.pair,
                    "opened": trade.open_date_utc.isoformat(),
                    "quantity": quantity,
                    "pending_usdt": pending,
                    "prior_close": float(close[-1]) if np.isfinite(close[-1]) else None,
                    "rho": rho,
                }
            )
        if eligible and capital > 0 and all(h["rho"] is not None for h in holdings):
            overlap = math.fsum(
                max(0.0, h["rho"]) * (h["quantity"] * h["prior_close"] + h["pending_usdt"])
                for h in holdings
            )
            ratio = 1.0 + 0.5 * max(0.0, 1 - overlap / (0.30 * capital))
        else:
            overlap, ratio = None, 1.0
        path = Path(self.config["user_data_dir"]) / "overlap-decisions.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": current_time.isoformat(),
                        "pair": pair,
                        "last_closed_day": cutoff.isoformat(),
                        "realized_capital_usdt": capital,
                        "longs": holdings,
                        "overlap_usdt": overlap,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return ratio, None
