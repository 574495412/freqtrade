"""Local dated limit-order quantity/notional rules, not venue certification."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D
from itertools import pairwise
from math import gcd

from ccxt import TICK_SIZE

from freqtrade.optimize.dated_price_precision import PriceRule, aware


@dataclass(frozen=True)
class AmountRule:
    pair: str
    valid_from: datetime
    valid_until: datetime
    known_at: datetime
    step: D
    minimum: D
    maximum: D
    minimum_notional: D
    source_sha256: str
    reduce_only_notional_exempt: bool

    def __post_init__(self):
        # Reuse the existing strict source/clock/native-float tick validation.
        PriceRule(
            self.pair,
            self.valid_from,
            self.valid_until,
            self.known_at,
            self.step,
            self.source_sha256,
        )
        for value in (self.minimum, self.maximum, self.minimum_notional):
            if not isinstance(value, D) or not value.is_finite() or value <= 0:
                raise ValueError("Finite positive Decimal amount/notional bounds required")
            if D(str(float(value))) != value:
                raise ValueError("Bound does not round-trip through native float")
        if self.minimum > self.maximum:
            raise ValueError("Minimum amount exceeds maximum")
        # Binance defines (quantity-minQty)%step. Native quantity accounting
        # starts at zero; do not silently claim support for an affine offset.
        if self.minimum % self.step:
            raise ValueError("Nonzero affine quantity grid requires native accounting extension")
        if type(self.reduce_only_notional_exempt) is not bool:
            raise ValueError("Explicit reduce-only notional policy required")


@dataclass(frozen=True)
class AmountRuleBook:
    pairs: tuple[str, ...]
    rules: tuple[AmountRule, ...]

    def __post_init__(self):
        if (
            not isinstance(self.pairs, tuple)
            or not self.pairs
            or len(set(self.pairs)) != len(self.pairs)
        ):
            raise ValueError("Frozen ordered unique amount universe required")
        if not isinstance(self.rules, tuple) or not self.rules:
            raise ValueError("Immutable amount rule tuple required")
        if any(not isinstance(r, AmountRule) or r.pair not in self.pairs for r in self.rules):
            raise ValueError("Amount rule outside universe")
        for pair in self.pairs:
            rows = sorted((r for r in self.rules if r.pair == pair), key=lambda r: r.valid_from)
            if not rows or any(a.valid_until > b.valid_from for a, b in pairwise(rows)):
                raise ValueError("Missing or overlapping amount rule intervals")

    def rule_at(self, pair, current_time):
        aware(current_time)
        rows = [
            r for r in self.rules if r.pair == pair and r.valid_from <= current_time < r.valid_until
        ]
        if len(rows) != 1 or rows[0].known_at > current_time:
            raise ValueError(f"Unknown dated amount rule for {pair} at {current_time}")
        return rows[0]

    def validate_window(self, start, end):
        aware(start)
        aware(end)
        if start >= end:
            raise ValueError("Nonempty amount rule window required")
        for pair in self.pairs:
            cursor = start
            for rule in sorted(
                (r for r in self.rules if r.pair == pair), key=lambda r: r.valid_from
            ):
                if rule.valid_until <= start or rule.valid_from >= end:
                    continue
                if rule.valid_from > cursor or rule.known_at > cursor:
                    raise ValueError(f"Incomplete known amount rule coverage for {pair}")
                cursor = min(rule.valid_until, end)
            if cursor < end:
                raise ValueError(f"Incomplete known amount rule coverage for {pair}")


class DatedAmountMixin:
    """Use before the dated-price/operation mixin in an opt-in native MRO."""

    def setup_dated_amount_rules(self, book):
        if not isinstance(book, AmountRuleBook):
            raise ValueError("Frozen AmountRuleBook required")
        if not hasattr(super(), "validate_order_amount"):
            raise ValueError("Local native final-order amount hooks required")
        self._amount_book = self._bound_amount_book = book
        self.amount_rule_trace = []
        self._require_amount_context()

    def _require_amount_context(self):
        self._require_dated_context()
        if (
            self.config.get("research_dated_amount_rules") is not True
            or self._amount_book is not self._bound_amount_book
            or self._amount_book.pairs != self._dated_price_book.pairs
            or self.precision_mode != TICK_SIZE
            or any(self.exchange.get_contract_size(p) != 1 for p in self._amount_book.pairs)
        ):
            raise ValueError("Frozen unit-contract dated quantity research context required")

    def _amount_rule(self, pair, current_time):
        self._require_amount_context()
        return self._amount_book.rule_at(pair, current_time)

    def get_entry_amount_precision(self, pair, current_time):
        return float(self._amount_rule(pair, current_time).step)

    def get_exit_amount_precision(self, trade, current_time):
        return float(self._amount_rule(trade.pair, current_time).step)

    def get_order_stake_limits(self, pair, current_time, price, stoploss, leverage):
        rule = self._amount_rule(pair, current_time)
        if leverage != 1.0:
            raise ValueError("Dated amount extension currently admits1x only")
        rate = D(str(price))
        if not rate.is_finite() or rate <= 0:
            raise ValueError("Finite positive native rate required")
        margin = D(1) + D(str(self.config.get("amount_reserve_percent", 0.05)))
        stop = D(str(abs(stoploss)))
        reserve = margin / (D(1) - stop) if stop != 1 else D("1.5")
        reserve = max(min(reserve, D("1.5")), D(1))
        minimum = max(rule.minimum_notional * reserve, rule.minimum * rate * margin)
        return float(minimum), float(rule.maximum * rate)

    def validate_order_amount(self, pair, current_time, amount, price, trade, is_exit):
        rule = self._amount_rule(pair, current_time)
        quantity, rate = D(str(amount)), D(str(price))
        reason = None
        if not quantity.is_finite() or not rate.is_finite() or quantity <= 0 or rate <= 0:
            reason = "nonpositive_or_nonfinite"
        elif (
            quantity < rule.minimum
            or quantity > rule.maximum
            or (quantity - rule.minimum) % rule.step
        ):
            reason = "quantity_bounds_or_grid"
        elif (
            not (is_exit and rule.reduce_only_notional_exempt)
            and quantity * rate < rule.minimum_notional
        ):
            reason = "minimum_notional_after_rounding"
        if is_exit and trade is not None and quantity > D(str(trade.amount)):
            reason = "exit_exceeds_position"
        self.amount_rule_trace.append(
            {
                "pair": pair,
                "at": current_time.isoformat(),
                "quantity": str(quantity),
                "price": str(rate),
                "is_exit": is_exit,
                "step": str(rule.step),
                "reason": reason,
                "accepted": reason is None,
            }
        )
        if reason and is_exit:
            # Terminal native force-exit assumes an Order was created. Abort an
            # unsupported dust account rather than process an old Order as exit.
            raise ValueError("Unsupported dated close amount: " + reason)
        return reason is None

    def update_trade_amount_precision(self, trade, order_precision):
        # This is an accounting lattice, distinct from the new order's legal
        # quantity step. Use only the prior lattice and the currently known step.
        old, new = D(str(trade.amount_precision)), D(str(order_precision))
        scale = D(10) ** max(-old.as_tuple().exponent, -new.as_tuple().exponent, 0)
        trade.amount_precision = float(D(gcd(int(old * scale), int(new * scale))) / scale)

    def can_reduce_position(self, trade, current_time, amount, price, min_stake):
        rule = self._amount_rule(trade.pair, current_time)
        remaining = D(str(trade.amount)) - D(str(amount))
        if remaining == 0:
            return True
        if remaining < rule.minimum or (remaining - rule.minimum) % rule.step:
            return False
        return (
            rule.reduce_only_notional_exempt or remaining * D(str(price)) >= rule.minimum_notional
        )
