"""Read-only net NAV reporting for the local native equity fee wallet.

This component creates neither trades nor account cash flows. It does not
enable EquityBacktesting, certify historical inputs or replace native gross
trade statistics. The dividend wallet can supply ordinary same-currency claims;
other corporate actions and external cash flows remain unsupported.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isclose, isfinite

import pandas as pd

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.data.metrics import calculate_max_drawdown_from_balance
from freqtrade.equity_fee_wallets import EquityFeeWallets
from freqtrade.persistence import Trade


def _clock(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EquityAdmissionError("Timezone-aware NAV clock required")
    return value


@dataclass(frozen=True)
class EquityMark:
    pair: str
    currency: str
    at: datetime
    price: float
    kind: str
    source_sha256: str

    def __post_init__(self):
        _clock(self.at)
        if self.kind not in {"open", "close"}:
            raise EquityAdmissionError("Explicit open or close mark required")
        if isinstance(self.price, bool) or not isfinite(self.price) or self.price <= 0:
            raise EquityAdmissionError("Finite positive observed mark required")
        if not self.pair.endswith("/" + self.currency):
            raise EquityAdmissionError("Mark currency differs from security")
        if len(self.source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.source_sha256
        ):
            raise EquityAdmissionError("Mark source bytes must be bound by SHA256")


@dataclass(frozen=True)
class EquityNavSnapshot:
    at: datetime
    market: str
    currency: str
    mark_kind: str
    initial_cash: float
    free_cash: float
    unsettled_receivable: float
    total_cash: float
    holdings: tuple[tuple[str, float, EquityMark], ...]
    market_value: float
    net_nav: float
    execution_fees: Decimal
    filled_orders: int
    closed_trade_net_cash_profit: Decimal
    # Each actual native Trade has its own complete order history. No FIFO or
    # invented closed-trade matching is used for open/partially exited trades.
    trade_fees: tuple[tuple[int, str, Decimal, bool], ...]
    dividend_receivable: Decimal = Decimal(0)
    paid_dividends: Decimal = Decimal(0)


def _validated_marks(wallet, marks, at, mark_kind):
    quotes = tuple(marks)
    by_pair = {}
    for mark in quotes:
        if not isinstance(mark, EquityMark) or mark.pair in by_pair:
            raise EquityAdmissionError("Unique explicit marks required")
        if mark.at != at or mark.kind != mark_kind or mark.currency != wallet.contract.currency:
            raise EquityAdmissionError("Exact-clock, same-currency and same-kind marks required")
        by_pair[mark.pair] = mark
    return by_pair


def _fees_and_closed_profit(wallet, trade):
    fees, net_flow = Decimal(0), Decimal(0)
    orders = set()
    for order in trade.orders:
        if not order.safe_filled:
            continue
        orders.add((trade.pair, order.order_id))
        quote = wallet.fees.quote(
            trade.pair,
            order.order_filled_utc,
            order.ft_order_side,
            order.safe_filled,
            order.safe_price,
        )
        fees += quote.total
        gross = Decimal(str(order.safe_filled)) * Decimal(str(order.safe_price))
        net_flow += (-gross if order.ft_order_side == "buy" else gross) - quote.total
    if not trade.is_open:
        # Native closed Trade.amount retains its last record quantity.
        quantity = sum(
            (
                wallet.quantity_asof(
                    trade.pair, Decimal(str(o.safe_filled)), o.order_filled_utc, wallet.asof
                ) * (1 if o.ft_order_side == "buy" else -1)
                for o in trade.orders
                if o.safe_filled
            ),
            Decimal(0),
        )
        if abs(quantity) > Decimal(".00000001"):
            raise EquityAdmissionError("Closed native trade is not flat by actual fills")
    return fees, net_flow if not trade.is_open else Decimal(0), orders


def _cash_partition(wallet):
    currency = wallet.contract.currency
    cash, free, locked = (
        wallet.get_total(currency),
        wallet.get_free(currency),
        wallet.get_used(currency),
    )
    if not isclose(cash, free + locked, rel_tol=0, abs_tol=1e-8):
        raise EquityAdmissionError("Native cash partitions do not reconcile")
    if not isclose(
        locked, wallet.cash_restriction.unsettled_sale_proceeds, rel_tol=0, abs_tol=1e-8
    ):
        raise EquityAdmissionError("Unexpected cash lock at completed fee-wallet boundary")
    return currency, cash, free, locked


def _dividend_balances(wallet):
    receivable, paid = wallet._dividend_nav_balances()
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in (receivable, paid)):
        raise EquityAdmissionError("Invalid net dividend NAV balances")
    return receivable, paid


def capture_equity_nav(wallet, marks, *, at, mark_kind):
    """Capture one completed native boundary without advancing its clock.

    All held securities require exact-clock marks; no stale/future quote carry,
    missing-price zero, estimated liquidation charge or FX conversion occurs.
    The caller owns the historical observation/publication-clock evidence.
    """
    _clock(at)
    if not isinstance(wallet, EquityFeeWallets) or at != wallet.asof:
        raise EquityAdmissionError("Current fee-aware native wallet boundary required")
    if mark_kind not in {"open", "close"}:
        raise EquityAdmissionError("Explicit NAV mark kind required")
    by_pair = _validated_marks(wallet, marks, at, mark_kind)
    wallet.update()
    trades = list(Trade.get_trades_proxy())
    ids = [t.id for t in trades]
    if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        raise EquityAdmissionError("Unique positive native trade ids required for attribution")
    holdings, trade_fees, closed_profit = [], [], Decimal(0)
    seen_pairs, seen_orders = set(), set()
    for trade in trades:
        if trade.is_open and trade.amount:
            if trade.pair in seen_pairs:
                raise EquityAdmissionError("Multiple open trades in one security unsupported")
            seen_pairs.add(trade.pair)
            if trade.pair not in by_pair:
                raise EquityAdmissionError("Missing current held-security mark")
            owned = wallet.get_owned(trade.pair, trade.pair.split("/")[0])
            if not isclose(owned, trade.amount, rel_tol=0, abs_tol=1e-8):
                raise EquityAdmissionError("Native held quantity does not reconcile")
            holdings.append((trade.pair, owned, by_pair[trade.pair]))
        fees, profit, orders = _fees_and_closed_profit(wallet, trade)
        seen_orders.update(orders)
        trade_fees.append((trade.id, trade.pair, fees, trade.is_open))
        closed_profit += profit
    if set(by_pair) != seen_pairs:
        raise EquityAdmissionError("Marks must cover exactly the actual native holdings")
    if sum((r[2] for r in trade_fees), Decimal(0)) != wallet.total_execution_fees:
        raise EquityAdmissionError("Per-trade fees do not reconcile with native wallet fees")
    currency, cash, free, locked = _cash_partition(wallet)
    value = sum(quantity * mark.price for _, quantity, mark in holdings)
    receivable, paid = _dividend_balances(wallet)
    nav = cash + value + float(receivable)
    if not isfinite(nav) or nav <= 0:
        raise EquityAdmissionError("Positive finite NAV required")
    return EquityNavSnapshot(
        at,
        wallet.contract.market,
        currency,
        mark_kind,
        wallet._start_cap[currency],
        free,
        locked,
        cash,
        tuple(holdings),
        value,
        nav,
        wallet.total_execution_fees,
        len(seen_orders),
        closed_profit,
        tuple(trade_fees),
        receivable,
        paid,
    )


class EquityNavSeries:
    """One declared native wallet and complete clock grid; no curve scaling.

    This is component evidence only. Admission of an actual historical account
    requires the still-incomplete owning execution/result lifecycle.
    """

    def __init__(self, wallet, *, expected_clocks, mark_kind):
        if not isinstance(wallet, EquityFeeWallets):
            raise EquityAdmissionError("Fee-aware native wallet required")
        clocks = tuple(_clock(t) for t in expected_clocks)
        if len(clocks) < 2 or tuple(sorted(set(clocks))) != clocks:
            raise EquityAdmissionError("At least two unique increasing declared clocks required")
        if mark_kind not in {"open", "close"}:
            raise EquityAdmissionError("Explicit NAV mark kind required")
        self.wallet, self.expected_clocks, self.mark_kind = wallet, clocks, mark_kind
        self._bound_wallet = wallet
        self._bound_fees, self._bound_cash = wallet.fees, wallet.contract
        self._bound_dividends = getattr(wallet, "dividends", None)
        self._initial_cash = wallet._start_cap[wallet.contract.currency]
        self._snapshots = []

    @property
    def snapshots(self):
        return tuple(self._snapshots)

    def capture(self, marks):
        if (
            self.wallet is not self._bound_wallet
            or self.wallet.fees is not self._bound_fees
            or self.wallet.contract is not self._bound_cash
            or getattr(self.wallet, "dividends", None) is not self._bound_dividends
            or self.wallet._start_cap[self.wallet.contract.currency] != self._initial_cash
        ):
            raise EquityAdmissionError("Native wallet, cash and fee contracts must stay fixed")
        index = len(self._snapshots)
        if index >= len(self.expected_clocks) or self.wallet.asof != self.expected_clocks[index]:
            raise EquityAdmissionError("Missing, duplicate or out-of-order NAV boundary")
        snapshot = capture_equity_nav(
            self.wallet,
            marks,
            at=self.expected_clocks[index],
            mark_kind=self.mark_kind,
        )
        if not index and (
            snapshot.holdings
            or snapshot.filled_orders
            or snapshot.execution_fees
            or not isclose(snapshot.net_nav, snapshot.initial_cash, rel_tol=0, abs_tol=1e-8)
        ):
            raise EquityAdmissionError("Account must begin at untouched, flat initial cash")
        if index and (
            snapshot.filled_orders < self._snapshots[-1].filled_orders
            or snapshot.execution_fees < self._snapshots[-1].execution_fees
            or snapshot.paid_dividends + snapshot.dividend_receivable
            < self._snapshots[-1].paid_dividends + self._snapshots[-1].dividend_receivable
        ):
            raise EquityAdmissionError("Native filled history or paid fees disappeared")
        self._snapshots.append(snapshot)
        return snapshot

    def component_result(self):
        if len(self._snapshots) != len(self.expected_clocks):
            raise EquityAdmissionError("Incomplete NAV grid has no full-window result")
        frame = pd.DataFrame(
            {
                "date": [s.at for s in self._snapshots],
                "net_nav": [s.net_nav for s in self._snapshots],
            }
        )
        dd = calculate_max_drawdown_from_balance(frame, balance_col="net_nav", relative=True)
        first, last = self._snapshots[0], self._snapshots[-1]
        return {
            "evidence": "native component only; not an admitted historical strategy account",
            "market": first.market,
            "currency": first.currency,
            "mark_kind": first.mark_kind,
            "start": first.at.isoformat(),
            "end": last.at.isoformat(),
            "snapshots": len(frame),
            "initial_cash": first.net_nav,
            "final_net_nav": last.net_nav,
            "net_change_ratio": last.net_nav / first.net_nav - 1,
            "sampled_max_drawdown": dd.relative_account_drawdown,
            "execution_fees": str(last.execution_fees),
            "terminal": "observed holdings mark" if last.holdings else "flat cash plus receivables",
            "terminal_unsettled_receivable": last.unsettled_receivable,
            "terminal_dividend_receivable": str(last.dividend_receivable),
            "paid_dividends": str(last.paid_dividends),
            "closed_trade_net_cash_profit": str(last.closed_trade_net_cash_profit),
            "historical_strategy_accounts": 0,
        }
