"""Research-only first-stake sizing on BTC-residual selloff recovery."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parent):
    from StrengthAllocation import StrengthAllocation


BTC = "BTC/USDT:USDT"


def measure(asset, market):
    """Input 68 consecutive positive UTC closes through the prior candle."""
    prices = np.asarray([asset, market], dtype=float)
    if prices.shape != (2, 68) or not np.isfinite(prices).all() or (prices <= 0).any():
        return None, None, None
    a, b = np.diff(np.log(prices), axis=1)
    x, y = b[:60] - b[:60].mean(), a[:60] - a[:60].mean()
    ss = np.dot(x, x)
    if ss <= 0:
        return None, None, None
    beta = float(np.dot(x, y) / ss)
    residual = a[-7:] - beta * b[-7:]
    return beta, float(residual.sum()), float(residual[-1])


def marked(recent, last):
    return recent is not None and recent <= math.log(0.97) and last > 0


class ResidualRecovery(StrengthAllocation):
    def multiplier(self, pair, current_time):
        last = pd.Timestamp(current_time).tz_convert("UTC").floor("D") - pd.Timedelta(days=1)
        days = pd.date_range(end=last, periods=68, freq="D")
        inputs = [self.exact_close(s).reindex(days).to_numpy() for s in (pair, BTC)]
        beta, residual, latest = measure(*inputs)
        path = Path(self.config["user_data_dir"]) / "residual-recovery.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "pair": pair,
                        "time": current_time.isoformat(),
                        "prior_closed_day": last.isoformat(),
                        "beta": beta,
                        "residual_7d": residual,
                        "residual_1d": latest,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return (2.0 if marked(residual, latest) else 1.0), None

    def exact_close(self, pair):
        if pair not in self.strength_prices:
            stem = pair.replace("/", "_").replace(":", "_")
            path = Path(self.config["datadir"]) / "futures" / f"{stem}-1d-futures.feather"
            self.strength_prices[pair] = pd.read_feather(path).set_index("date").close
        return self.strength_prices[pair]
