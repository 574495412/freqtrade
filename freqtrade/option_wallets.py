"""Coin premium cash and S:SM state from actual native Trade/Order fills.

This local Wallets subclass owns option cash semantics. Native fill matching,
Trade/Order objects and lifecycle remain native. Generic futures stake/PnL is
not option cash. No complete historical backtest or venue liquidation claim.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite

from freqtrade.data.inverse_option_contracts import OptionAdmissionError, aware
from freqtrade.data.option_cash import OptionCashContract, option_margin
from freqtrade.enums import RunMode, TradingMode
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallet, Wallets


def _d(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
        raise OptionAdmissionError("Finite actual native numeric field required")
    return Decimal(str(value))


@dataclass(frozen=True)
class NativeOptionFill:
    pair: str
    trade_id: int
    native_order_id: int
    order_id: str
    at: datetime
    side: str
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class OptionCashState:
    at: datetime
    currency: str
    initial_cash: Decimal
    premium_cash_flow: Decimal
    execution_fees: Decimal
    cash_balance: Decimal
    option_value: Decimal
    equity: Decimal
    margin_balance: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    pending_reserve: Decimal
    available_funds: Decimal
    positions: tuple[tuple[str, Decimal], ...]
    initial_margin_breach: bool
    liquidation_required: bool


class OptionPremiumWallets(Wallets):
    def __init__(self, config, exchange, *, contract, asof, marks=()):
        if not isinstance(contract, OptionCashContract):
            raise OptionAdmissionError("Explicit option cash contract required")
        self.contract = self._bound_contract = contract
        self._check_isolation(config)
        contract.require_clock(asof)
        if asof != contract.starts or Trade.get_trades_proxy():
            raise OptionAdmissionError("Option component starts flat at its declared boundary")
        self.asof, self._marks = asof, contract.marks(marks, asof)
        self._history, self.fill_journal = {}, ()
        self.cash_state = None
        super().__init__(config, exchange, is_backtest=True)

    def _check_isolation(self, config):
        if (
            config.get("option_wallet_component") is not True
            or config.get("dry_run") is not True
            or config.get("runmode") != RunMode.BACKTEST
            or config.get("trading_mode") != TradingMode.FUTURES
            or Trade.use_db
            or config.get("stake_currency") != self.contract.currency
            or config.get("tradable_balance_ratio") != 1
        ):
            raise OptionAdmissionError("Isolated BACKTEST option component/native container only")
        cap = config.get("dry_run_wallet")
        if (
            isinstance(cap, bool)
            or not isinstance(cap, (int, float))
            or not isfinite(cap)
            or cap <= 0
        ):
            raise OptionAdmissionError("Explicit finite positive single-coin initial cash required")
        if config.get("available_capital") is not None:
            raise OptionAdmissionError("No parallel capital or external transfers supported")

    def update(self, require_update=True):
        self._check_isolation(self._config)
        return super().update(True)

    def _update_live(self):
        raise OptionAdmissionError("Option cash component cannot read live balances")

    def advance(self, asof, marks):
        self.contract.require_clock(asof)
        if asof < self.asof:
            raise OptionAdmissionError("Option wallet clock cannot move backwards")
        previous = self.asof, self._marks
        self.asof, self._marks = asof, self.contract.marks(marks, asof)
        try:
            self.update()
        except Exception:
            self.asof, self._marks = previous
            raise

    def _native_orders(self):
        fills, pending, meta, seen = {}, [], {}, set()
        for trade in Trade.get_trades_proxy():
            self.contract.spec(trade.pair)
            if type(trade.id) is not int or trade.id <= 0 or trade.id in meta:
                raise OptionAdmissionError("Unique positive native trade identities required")
            if (
                trade.trading_mode != TradingMode.FUTURES
                or trade.leverage != 1
                or trade.fee_open != 0
                or trade.fee_close != 0
                or trade.funding_fees
                or trade.funding_fee_running
                or trade.open_date_utc > self.asof
            ):
                raise OptionAdmissionError(
                    "Native container must have zero percentage fees/funding and leverage1"
                )
            meta[trade.id] = trade.pair, trade.is_short
            for order in trade.orders:
                key, quantity = self._check_order(trade, order, seen)
                if quantity:
                    fills[key] = self._fill(trade, order)
                elif order.ft_is_open:
                    if order.order_date_utc > self.asof:
                        raise OptionAdmissionError("Future native pending order")
                    pending.append((trade, order))
        if any(fills.get(k) != old for k, old in self._history.items()):
            raise OptionAdmissionError("Native filled option history changed or vanished")
        return fills, pending, meta

    @staticmethod
    def _check_order(trade, order, seen):
        key = trade.pair, order.order_id
        if key in seen or not order.order_id or type(order.id) is not int or order.id <= 0:
            raise OptionAdmissionError("Unique actual native option order identities required")
        seen.add(key)
        quantity = _d(order.safe_filled)
        if quantity < 0 or order.ft_pair != trade.pair:
            raise OptionAdmissionError("Invalid native option order quantity/pair")
        if order.ft_order_side not in {"buy", "sell"} or order.order_type != "limit":
            raise OptionAdmissionError("Only explicit buy/sell limit option fills supported")
        if order.safe_fee_base or order.funding_fee:
            raise OptionAdmissionError("Native base fees/funding would double count option cash")
        return key, quantity

    def _fill(self, trade, order):
        when = order.order_filled_utc
        if when is None or aware(when) > self.asof or when < trade.open_date_utc:
            raise OptionAdmissionError("Actual observed native fill clock required")
        if order.ft_is_open or order.status != "closed" or order.safe_remaining:
            raise OptionAdmissionError("Only atomic native order fills supported")
        quantity, price = _d(order.safe_filled), _d(order.safe_price)
        self.contract.spec(trade.pair).validate_known_order_limits(quantity, price)
        self.contract.require_clock(when)
        return NativeOptionFill(
            trade.pair,
            trade.id,
            order.id,
            order.order_id,
            when,
            order.ft_order_side,
            quantity,
            price,
        )

    def _evaluate(self, fills, pending, meta):
        cash_flow, fees = Decimal(0), Decimal(0)
        by_trade = {key: Decimal(0) for key in meta}
        for row in sorted(fills.values(), key=lambda x: (x.at, x.native_order_id)):
            delta = row.quantity if row.side == "buy" else -row.quantity
            by_trade[row.trade_id] += delta
            if (meta[row.trade_id][1] and by_trade[row.trade_id] > 0) or (
                not meta[row.trade_id][1] and by_trade[row.trade_id] < 0
            ):
                raise OptionAdmissionError("Option exits cannot reverse a native trade")
            cash_flow -= delta * row.price
            fees += self.contract.fee(row.pair, row.at, row.quantity, row.price)
        positions = {}
        for identity, qty in by_trade.items():
            if qty:
                pair = meta[identity][0]
                if pair in positions:
                    raise OptionAdmissionError("One native open trade per option;no hidden netting")
                positions[pair] = qty
        value, initial, maintenance = Decimal(0), Decimal(0), Decimal(0)
        for pair, qty in positions.items():
            mark = self._mark(pair)
            value += qty * mark.premium
            im, mm = option_margin(self.contract.spec(pair), qty, mark)
            initial, maintenance = initial + im, maintenance + mm
        reserve = self._pending_reserve(pending)
        capital = Decimal(str(self._start_cap[self._stake_currency]))
        cash = capital + cash_flow - fees
        free = cash - initial - reserve
        return OptionCashState(
            self.asof,
            self.contract.currency,
            capital,
            cash_flow,
            fees,
            cash,
            value,
            cash + value,
            cash,
            initial,
            maintenance,
            reserve,
            free,
            tuple(sorted(positions.items())),
            cash < initial + reserve,
            cash < maintenance,
        )

    def _mark(self, pair):
        mark = self._marks.get(pair)
        if mark is None:
            raise OptionAdmissionError(
                "Current observed mark/index required for every option exposure"
            )
        return mark

    def _pending_reserve(self, pending):
        reserve = Decimal(0)
        for trade, order in pending:
            qty, price = _d(order.safe_amount), _d(order.safe_price)
            self.contract.spec(trade.pair).validate_known_order_limits(qty, price)
            if order.ft_order_side != trade.entry_side:
                continue  # Held liability/margin is not released until actual exit fill.
            fee = self.contract.fee(trade.pair, self.asof, qty, price)
            if trade.is_short:
                im, _ = option_margin(self.contract.spec(trade.pair), -qty, self._mark(trade.pair))
                reserve += im + fee  # Explicit LOCAL conservative model:no unfilled sale credit.
            else:
                reserve += qty * price + fee
        return reserve

    def _update_dry(self):
        if self.contract is not self._bound_contract:
            raise OptionAdmissionError("Bound option cash contract cannot switch")
        self.contract.require_clock(self.asof)
        fills, pending, meta = self._native_orders()
        state = self._evaluate(fills, pending, meta)
        balance = Wallet(
            self._stake_currency,
            float(state.available_funds),
            float(state.initial_margin + state.pending_reserve),
            float(state.cash_balance),
        )
        self._wallets, self._positions = {self._stake_currency: balance}, {}
        self.cash_state = state
        self.fill_journal = tuple(sorted(fills.values(), key=lambda f: (f.at, f.native_order_id)))
        self._history = fills

    def require_fill_capacity(self, trade, order):
        if self.cash_state.liquidation_required:
            raise OptionAdmissionError(
                "Maintenance breach requires unimplemented venue liquidation"
            )
        fills, pending, meta = self._native_orders()
        quantity, price = _d(order.safe_amount), _d(order.safe_price)
        self.contract.spec(trade.pair).validate_known_order_limits(quantity, price)
        key = trade.pair, order.order_id
        projected = NativeOptionFill(
            trade.pair,
            trade.id,
            order.id,
            order.order_id,
            self.asof,
            order.ft_order_side,
            quantity,
            price,
        )
        fills = fills.copy()
        fills[key] = projected
        pending = [(t, o) for t, o in pending if (t.pair, o.order_id) != key]
        state = self._evaluate(fills, pending, meta)
        entry = order.ft_order_side == trade.entry_side
        if (
            state.cash_balance < 0
            or (entry and state.available_funds < 0)
            or state.liquidation_required
        ):
            raise OptionAdmissionError(
                "Prospective native fill exceeds option cash/margin including fees"
            )

    def get_available_stake_amount(self):
        return max(0.0, float(self.cash_state.available_funds))

    def get_total_stake_amount(self):
        return float(self.cash_state.margin_balance)

    def get_starting_balance(self):
        return float(self._start_cap[self._stake_currency])

    def get_owned(self, pair, base_currency):
        self.contract.spec(pair)
        return float(dict(self.cash_state.positions).get(pair, Decimal(0)))
