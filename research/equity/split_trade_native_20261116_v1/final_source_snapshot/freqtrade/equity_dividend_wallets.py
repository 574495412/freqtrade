"""Dividend entitlements from actual native fills; no separate account simulator.

Cumulative credits enter the existing settled cash hook before cash validation,
so actual native buys can use paid dividends. Unpaid claims remain outside cash.
This component does not open the incomplete EquityBacktesting entry point.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from freqtrade.data.equity_dividends import DividendContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_fee_wallets import EquityFeeWallets
from freqtrade.persistence import Trade


@dataclass(frozen=True)
class DividendEntitlement:
    identity: str
    pair: str
    ex_open: datetime
    quantity: Decimal
    # Attribution identifies the actual native Trade; it does not match synthetic lots.
    trade_quantities: tuple[tuple[int, Decimal], ...]
    gross: Decimal
    withholding: Decimal
    net: Decimal
    available_at: datetime | None
    paid: bool


class EquityDividendWallets(EquityFeeWallets):
    def __init__(self, config, exchange, *, fees, dividends, asof):
        if not isinstance(dividends, DividendContract) or dividends.cash != fees.cash:
            raise EquityAdmissionError("Dividend and fee cash contracts must agree")
        if set(dividends.pairs) != set(fees.pairs):
            raise EquityAdmissionError("Dividend and fee security coverage must agree")
        dividends.require_clock(asof)
        if asof != dividends.coverage_start:
            raise EquityAdmissionError("Dividend wallet must begin at declared coverage start")
        if Trade.get_trades_proxy():
            raise EquityAdmissionError("Dividend account starts flat without inherited claims")
        self.dividends = self._bound_dividends = dividends
        self.dividend_journal = ()
        self.paid_dividends = self.dividend_receivable = Decimal(0)
        self._filled_history = {}
        super().__init__(config, exchange, fees=fees, asof=asof)

    def _additional_settled_cash(self):
        return float(self.paid_dividends)

    def _dividend_nav_balances(self):
        return self.dividend_receivable, self.paid_dividends

    def _actual_history(self):
        history, trades = {}, list(Trade.get_trades_proxy())
        ids = [t.id for t in trades]
        if any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
            raise EquityAdmissionError("Unique actual native trade ids required for dividends")
        for trade in trades:
            if trade.pair not in self.dividends.pairs:
                raise EquityAdmissionError("Native holding outside dividend input coverage")
            for order in trade.orders:
                if not order.safe_filled:
                    continue
                key = (trade.pair, order.order_id)
                if not order.order_id or key in history:
                    raise EquityAdmissionError("Duplicate actual dividend fill identity")
                when = order.order_filled_utc
                if when is None or when > self.asof:
                    raise EquityAdmissionError("Future or missing dividend fill clock")
                history[key] = (
                    trade.id, trade.pair, when, order.ft_order_side,
                    Decimal(str(order.safe_filled)), Decimal(str(order.safe_price)),
                )
        if any(history.get(key) != old for key, old in self._filled_history.items()):
            raise EquityAdmissionError("Actual dividend fill history changed or vanished")
        return history

    def _entitlements(self, history):
        rows = []
        for event in self.dividends.events:
            if event.ex_open > self.asof:
                continue
            by_trade = {}
            for identity, pair, when, side, quantity, _ in history.values():
                if pair == event.pair and when < event.ex_open:
                    if side not in {"buy", "sell"}:
                        raise EquityAdmissionError("Unknown dividend fill side")
                    effective = self.quantity_asof(pair, quantity, when, event.ex_open)
                    by_trade[identity] = by_trade.get(identity, Decimal(0)) + (
                        effective if side == "buy" else -effective
                    )
            if any(q < 0 for q in by_trade.values()):
                raise EquityAdmissionError("Negative actual entitlement quantity")
            quantity = sum(by_trade.values(), Decimal(0))
            if not quantity:
                continue
            gross, tax, net = event.amounts(quantity)
            payment_known = (
                event.payment_known_at is not None and event.payment_known_at <= self.asof
            )
            available = event.available_at if payment_known else None
            rows.append(DividendEntitlement(
                event.identity, event.pair, event.ex_open, quantity,
                tuple(sorted((i, q) for i, q in by_trade.items() if q)),
                gross, tax, net, available, available is not None and available <= self.asof,
            ))
        return tuple(rows)

    def _update_dry(self):
        if self.dividends is not self._bound_dividends or self.contract != self.dividends.cash:
            raise EquityAdmissionError("Dividend account contract cannot switch")
        self.dividends.require_clock(self.asof)
        history = self._actual_history()
        journal = self._entitlements(history)
        paid = sum((r.net for r in journal if r.paid), Decimal(0))
        receivable = sum((r.net for r in journal if not r.paid), Decimal(0))
        previous = (self.dividend_journal, self.paid_dividends, self.dividend_receivable)
        self.dividend_journal = journal
        self.paid_dividends, self.dividend_receivable = paid, receivable
        try:
            super()._update_dry()
        except Exception:
            self.dividend_journal, self.paid_dividends, self.dividend_receivable = previous
            raise
        self._filled_history = history
