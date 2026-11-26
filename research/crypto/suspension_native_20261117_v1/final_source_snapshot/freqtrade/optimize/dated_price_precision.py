"""Local opt-in dated price rules for native backtests, not venue certification.

Rule intervals must be supplied with explicit source and knowledge clocks.
This module never derives rules from future OHLC or fills missing intervals.
It covers price normalization only; amount/notional/other venue filters remain
separate requirements before any financial account can be admitted.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise

from ccxt import TICK_SIZE

from freqtrade.enums import MarginMode, RunMode, TradingMode
from freqtrade.exchange import price_to_precision
from freqtrade.persistence import Trade


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware execution/knowledge clock is required")
    return value


@dataclass(frozen=True)
class PriceRule:
    pair: str
    valid_from: datetime
    valid_until: datetime
    known_at: datetime
    tick: Decimal
    source_sha256: str
    suspended: bool = False

    def __post_init__(self):
        for clock in (self.valid_from, self.valid_until, self.known_at):
            aware(clock)
        if self.valid_from >= self.valid_until:
            raise ValueError("Nonempty half-open validity interval required")
        if not isinstance(self.tick, Decimal) or not self.tick.is_finite() or self.tick <= 0:
            raise ValueError("Finite positive Decimal tick required")
        value = float(self.tick)
        if not math.isfinite(value) or value <= 0 or Decimal(str(value)) != self.tick:
            raise ValueError("Tick cannot round-trip through native float precision")
        if not self.pair or len(self.source_sha256) != 64:
            raise ValueError("Source-bound pair and SHA256 required")
        try:
            int(self.source_sha256, 16)
        except ValueError as error:
            raise ValueError("Invalid source SHA256") from error
        if type(self.suspended) is not bool:
            raise ValueError("Explicit suspension state required")


@dataclass(frozen=True)
class PriceRuleBook:
    pairs: tuple[str, ...]
    rules: tuple[PriceRule, ...]

    def __post_init__(self):
        if (
            not isinstance(self.pairs, tuple)
            or not self.pairs
            or len(set(self.pairs)) != len(self.pairs)
        ):
            raise ValueError("Frozen ordered unique pairs required")
        if not isinstance(self.rules, tuple) or not self.rules:
            raise ValueError("Immutable source-bound rule tuple required")
        if any(
            not isinstance(rule, PriceRule) or rule.pair not in self.pairs for rule in self.rules
        ):
            raise ValueError("Rule pair outside the frozen universe")
        for pair in self.pairs:
            rows = sorted((r for r in self.rules if r.pair == pair), key=lambda r: r.valid_from)
            if not rows:
                raise ValueError(f"No rules for {pair}")
            if any(a.valid_until > b.valid_from for a, b in pairwise(rows)):
                raise ValueError("Overlapping rules require explicit version resolution")

    def rule_at(self, pair, current_time):
        aware(current_time)
        rows = [
            r for r in self.rules if r.pair == pair and r.valid_from <= current_time < r.valid_until
        ]
        if len(rows) != 1 or rows[0].known_at > current_time:
            raise ValueError(f"Unknown dated price rule for {pair} at {current_time}")
        if rows[0].suspended:
            raise ValueError(f"Order operations suspended for {pair} at {current_time}")
        return rows[0]

    def validate_window(self, start, end):
        """Require full known coverage; unsupported suspension stops admission."""
        aware(start)
        aware(end)
        if start >= end:
            raise ValueError("Nonempty backtest window required")
        for pair in self.pairs:
            cursor = start
            rows = sorted((r for r in self.rules if r.pair == pair), key=lambda r: r.valid_from)
            for rule in rows:
                if rule.valid_until <= start or rule.valid_from >= end:
                    continue
                if rule.valid_from > cursor or rule.known_at > cursor:
                    raise ValueError(f"Incomplete known coverage for {pair}")
                if rule.suspended:
                    raise ValueError(f"Sub-candle suspension integration required for {pair}")
                cursor = min(rule.valid_until, end)
            if cursor < end:
                raise ValueError(f"Incomplete known coverage for {pair}")


class DatedPricePrecisionMixin:
    """Use with the local extended native Backtesting, never a live order engine."""

    def setup_dated_price_rules(self, book):
        if not isinstance(book, PriceRuleBook):
            raise ValueError("A frozen PriceRuleBook is required")
        if not hasattr(super(), "get_exit_order_precision") or not hasattr(
            super(), "normalize_entry_order_rate"
        ):
            raise ValueError("Local native order-precision hooks are required")
        self._dated_price_book = book
        self._bound_price_book = book
        self._require_dated_context()

    def _require_dated_context(self):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or self.config.get("dry_run") is not True
            or self.config.get("research_dated_price_rules") is not True
            or Trade.use_db
            or self.trading_mode != TradingMode.FUTURES
            or self.margin_mode != MarginMode.ISOLATED
            or self.exchange.precision_mode_price != TICK_SIZE
            or self.config["exchange"].get("name") != "binance"
            or self._dated_price_book is not self._bound_price_book
            or tuple(self.config["exchange"]["pair_whitelist"]) != self._dated_price_book.pairs
        ):
            raise ValueError("Frozen offline Binance isolated-futures research contract required")

    def get_pair_precision(self, pair, current_time):
        self._require_dated_context()
        rule = self._dated_price_book.rule_at(pair, current_time)
        return float(rule.tick), TICK_SIZE

    def normalize_entry_order_rate(self, pair, current_time, rate, order_type):
        if order_type != "limit":
            raise ValueError("Dated price-rule extension currently admits limit orders only")
        tick, mode = self.get_pair_precision(pair, current_time)
        return price_to_precision(rate, tick, mode)

    def get_exit_order_precision(self, trade, current_time):
        if self.strategy.order_types["exit"] != "limit":
            raise ValueError("Dated price-rule extension currently admits limit exits only")
        return self.get_pair_precision(trade.pair, current_time)

    def _try_close_open_order(self, order, trade, current_time, row):
        # Existing pending orders retain their original price after a tick change.
        # A known suspension aborts this unsupported account, rather than inventing
        # fills inside a partially suspended OHLC candle.
        self.get_pair_precision(trade.pair, current_time)
        return super()._try_close_open_order(order, trade, current_time, row)

    def check_order_cancel(self, trade, order, current_time):
        self.get_pair_precision(trade.pair, current_time)
        return super().check_order_cancel(trade, order, current_time)

    def cancel_open_orders(self, trade, current_time):
        self.get_pair_precision(trade.pair, current_time)
        return super().cancel_open_orders(trade, current_time)
