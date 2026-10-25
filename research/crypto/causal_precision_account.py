"""Native matching with an explicitly causal, candle-derived precision proxy."""

from pathlib import Path

import pandas as pd

from freqtrade.exchange.exchange import TICK_SIZE
from research.crypto.first_hit_paths import causal_ticks
from research.crypto.run_nfi import ObservedBacktesting


class CausalPrecisionBacktesting(ObservedBacktesting):
    def __init__(self, config, dataset):
        self.closed_precision = {}
        for pair in config["exchange"]["pair_whitelist"]:
            stem = pair.replace("/", "_").replace(":", "_")
            prices = pd.read_feather(
                Path(dataset) / f"candles/futures/{stem}-5m-futures.feather"
            ).set_index("date")
            prices = prices.loc[prices.index >= pd.Timestamp("2023-12-01", tz="UTC")]
            self.closed_precision[pair] = causal_ticks(prices)
        super().__init__(config, dataset)

    def get_pair_precision(self, pair, current_time):
        tick = self.closed_precision[pair].asof(current_time)
        if pd.isna(tick) or tick <= 0:
            raise ValueError("No already-closed candle precision proxy")
        return float(tick), TICK_SIZE
