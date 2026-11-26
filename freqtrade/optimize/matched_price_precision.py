"""Local opt-in BTC/ETH spot/futures snapshot ticks for matched research.

This does not replace the order engine or certify historical venue filters.
The frozen snapshots are explicit model assumptions, never future-month price
extrema. Ordinary crypto/live and previously sealed carry paths are unchanged.
"""

import math

from ccxt import TICK_SIZE

from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade


class MatchedPricePrecisionMixin:
    def setup_frozen_price_precision(self, *, pairs):
        if not isinstance(pairs, tuple) or len(pairs) != 2 or len(set(pairs)) != 2:
            raise ValueError("Exactly two frozen ordered pairs required")
        self._matched_pairs = pairs
        self._matched_mode = self.trading_mode
        self._require_matched_context()
        self._frozen_price_ticks = {p: self.exchange.get_precision_price(p) for p in pairs}
        if any(isinstance(t, bool) or not isinstance(t, (int, float))
               or not math.isfinite(t) or t <= 0 for t in self._frozen_price_ticks.values()):
            raise ValueError("Finite positive snapshot ticks required")

    def _require_matched_context(self):
        expected = ("BTC/USDT", "ETH/USDT")
        if self.trading_mode == TradingMode.FUTURES:
            expected = tuple(p+":USDT" for p in expected)
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or self.config.get("dry_run") is not True
            or self.config.get("research_matched_nfi") is not True
            or Trade.use_db or self.trading_mode not in (TradingMode.SPOT, TradingMode.FUTURES)
            or self.trading_mode != self._matched_mode
            or self.exchange.precision_mode_price != TICK_SIZE
            or self._matched_pairs != expected
            or tuple(self.config["exchange"]["pair_whitelist"]) != expected
        ):
            raise ValueError("Declared BTC/ETH native backtest-only snapshot contract required")

    def get_pair_precision(self, pair, current_time):
        self._require_matched_context()
        if (pair not in self._frozen_price_ticks or current_time.tzinfo is None
            or current_time.utcoffset() is None
            or self.exchange.get_precision_price(pair) != self._frozen_price_ticks[pair]):
            raise ValueError("Snapshot pair/tick/aware clock changed")
        return self._frozen_price_ticks[pair], TICK_SIZE
