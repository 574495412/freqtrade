"""Local opt-in frozen price ticks for segregated native spot/futures carry legs."""

import math

from ccxt import TICK_SIZE

from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade


class CarryPrecisionMixin:
    def setup_frozen_price_precision(self, *, pairs):
        self._carry_pairs = tuple(pairs)
        self._carry_mode = self.trading_mode
        self._require_carry_precision()
        self._frozen_price_ticks = {p: self.exchange.get_precision_price(p) for p in pairs}
        if any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0
            for x in self._frozen_price_ticks.values()
        ):
            raise ValueError("Positive frozen market price ticks required")

    def _require_carry_precision(self):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or not self.config.get("research_carry_leg")
            or Trade.use_db
            or self.trading_mode not in (TradingMode.SPOT, TradingMode.FUTURES)
            or self.trading_mode != self._carry_mode
            or self.exchange.precision_mode_price != TICK_SIZE
            or len(self._carry_pairs) != 1
            or tuple(self.config["exchange"]["pair_whitelist"]) != self._carry_pairs
        ):
            raise ValueError("One segregated native research carry leg required")

    def get_pair_precision(self, pair, current_time):
        self._require_carry_precision()
        if (
            pair not in self._frozen_price_ticks
            or current_time.tzinfo is None
            or self.exchange.get_precision_price(pair) != self._frozen_price_ticks[pair]
        ):
            raise ValueError("Frozen leg price contract changed")
        return self._frozen_price_ticks[pair], TICK_SIZE
