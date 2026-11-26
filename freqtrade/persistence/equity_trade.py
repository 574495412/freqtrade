"""Split-unit view of the native LocalTrade, retaining original Order evidence.

Only single-entry/full-exit long cash trades and integral remaining entitlements
are admitted. Native recalc/profit methods operate on temporary read-only views;
actual filled orders and their cash amounts are never rewritten or fabricated.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from freqtrade.data.equity_splits import ShareSplitContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import TradingMode
from freqtrade.persistence import LocalTrade


@dataclass(frozen=True)
class _UnitOrder:
    ft_is_open: bool
    filled: float
    funding_fee: float
    safe_amount_after_fee: float
    safe_price: float
    ft_order_side: str


class EquitySplitTrade(LocalTrade):
    def __init__(self, *, splits, **kwargs):
        if not isinstance(splits, ShareSplitContract) or kwargs.get("pair") not in splits.pairs:
            raise EquityAdmissionError("Explicit implemented unit contract required")
        if (
            kwargs.get("is_short")
            or kwargs.get("leverage") != 1
            or kwargs.get("trading_mode") != TradingMode.SPOT
            or kwargs.get("fee_open") != 0
            or kwargs.get("fee_close") != 0
        ):
            raise EquityAdmissionError("Split trade supports unlevered fee-overlay long cash only")
        self.splits = splits
        self.unit_asof = kwargs["open_date"]
        splits.require_clock(self.unit_asof)
        self._filled_unit_history = {}
        self.unit_journal = ()
        super().__init__(**kwargs)

    def recalc_open_trade_value(self):
        if self.amount > 0 and self.stake_amount > 0:
            # A cost average is not a traded quote and must not be rounded to
            # the current exchange tick after, for example, a3-for-1split.
            self.open_rate = self.stake_amount / self.amount
        super().recalc_open_trade_value()

    def _unit_orders(self):
        views, history, entries, exits = [], {}, 0, 0
        for order in self.orders:
            if order.ft_is_open:
                continue
            if not order.safe_filled:
                continue
            when = order.order_filled_utc
            if when is None or when > self.unit_asof or order.safe_fee_base or order.funding_fee:
                raise EquityAdmissionError("Atomic past native quote-fee fills required")
            if order.ft_order_side not in {"buy", "sell"}:
                raise EquityAdmissionError("Unknown native split-unit fill side")
            entries += int(order.ft_order_side == "buy")
            exits += int(order.ft_order_side == "sell")
            key = order.order_id
            if not key or key in history:
                raise EquityAdmissionError("Unique immutable native fill identities required")
            history[key] = (when, order.ft_order_side, order.safe_filled, order.safe_price)
            ratio = self.splits.ratio(self.pair, when, self.unit_asof)
            quantity = Fraction(Decimal(str(order.safe_amount_after_fee))) * ratio
            price = Fraction(Decimal(str(order.safe_price))) / ratio
            views.append(
                _UnitOrder(
                    False, float(quantity), 0.0, float(quantity), float(price), order.ft_order_side
                )
            )
        if entries > 1 or exits > 1:
            raise EquityAdmissionError("DCA/partial-exit split accounting is not admitted")
        if any(history.get(key) != row for key, row in self._filled_unit_history.items()):
            raise EquityAdmissionError("Original native filled orders changed or disappeared")
        return views, history

    def recalc_trade_from_orders(self, *, is_closing=False):
        previous = self.__dict__.copy()
        originals = self.orders
        try:
            views, history = self._unit_orders()
            current = sum(
                (
                    Fraction(str(o.safe_amount_after_fee)) * (1 if o.ft_order_side == "buy" else -1)
                    for o in views
                ),
                Fraction(0),
            )
            if current < 0 or current.denominator != 1:
                raise EquityAdmissionError(
                    "Fractional/negative split entitlement needs a cash-in-lieu contract"
                )
            self.orders = views
            super().recalc_trade_from_orders(is_closing=is_closing)
            self._filled_unit_history = history
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(previous)
            raise
        finally:
            self.orders = originals

    def advance_units(self, at):
        self.splits.require_clock(at)
        if at < self.unit_asof:
            raise EquityAdmissionError("Native split-unit clock cannot move backwards")
        if not self.is_open or at == self.unit_asof:
            return
        if self.has_open_orders:
            raise EquityAdmissionError("Carried orders require a separate split order contract")
        ratio = self.splits.ratio(self.pair, self.unit_asof, at)
        prospective = Fraction(Decimal(str(self.amount))) * ratio
        if prospective.denominator != 1:
            raise EquityAdmissionError("Fractional split entitlement needs a cash-in-lieu contract")
        previous = self.__dict__.copy()
        try:
            before = self.amount
            self.unit_asof = at
            if ratio != 1:
                for field in ("stop_loss", "initial_stop_loss", "max_rate", "min_rate"):
                    value = getattr(self, field)
                    if value is not None:
                        setattr(self, field, float(Fraction(Decimal(str(value))) / ratio))
            self.recalc_trade_from_orders()
            if self.amount != float(prospective):
                raise EquityAdmissionError("Native split quantity did not reconcile")
            if ratio != 1:
                self.unit_journal += (
                    (at, before, self.amount, ratio.numerator, ratio.denominator),
                )
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(previous)
            raise
