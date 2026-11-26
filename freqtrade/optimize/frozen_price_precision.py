"""Local opt-in fixed-snapshot price ticks for isolated native research accounts.

Do not infer today's order precision from future candles in the same month.
This is an explicit current-snapshot historical scenario, not certification of
historical venue filter changes. Native rounding, orders and matcher remain.
"""
import math

from ccxt import TICK_SIZE

from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade


class FrozenPricePrecisionMixin:
    def setup_frozen_price_precision(self, *, pairs):
        if not isinstance(pairs, tuple) or len(set(pairs)) != len(pairs) or not pairs:
            raise ValueError('Unique ordered frozen pair universe required')
        self._frozen_price_pairs = pairs
        self._require_frozen_price_context()
        ticks = {pair:self.exchange.get_precision_price(pair) for pair in pairs}
        if any(isinstance(t,bool) or not isinstance(t,(int,float))
               or not math.isfinite(t) or t <= 0 for t in ticks.values()):
            raise ValueError('Explicit positive finite snapshot ticks required')
        self._frozen_price_ticks = ticks

    def _require_frozen_price_context(self):
        if (self.config.get('runmode') != RunMode.BACKTEST or self.config.get('dry_run') is not True
                or self.trading_mode != TradingMode.FUTURES
                or self.exchange.precision_mode_price != TICK_SIZE or Trade.use_db
                or tuple(self.config['exchange']['pair_whitelist']) != self._frozen_price_pairs):
            raise ValueError('Frozen tick contract requires isolated native futures backtest')

    def get_pair_precision(self, pair, current_time):
        if not hasattr(self,'_frozen_price_ticks'):
            raise ValueError('Frozen price precision was not explicitly configured')
        self._require_frozen_price_context()
        if (pair not in self._frozen_price_ticks or current_time.tzinfo is None
                or current_time.utcoffset() is None):
            raise ValueError('Declared security and aware execution clock required')
        tick = self._frozen_price_ticks[pair]
        if self.exchange.get_precision_price(pair) != tick:
            raise ValueError('Frozen exchange price precision changed')
        return tick, TICK_SIZE
