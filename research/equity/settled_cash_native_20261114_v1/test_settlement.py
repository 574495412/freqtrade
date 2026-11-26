"""Synthetic native execution checks, never financial strategy evidence."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bootstrap import boot


boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import MarginMode, RunMode, TradingMode
from freqtrade.equity_wallets import (
    CashSession,
    EquityCashContract,
    EquitySettlementWallets,
    SettlementDay,
)
from freqtrade.exceptions import DependencyException
from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.persistence import LocalTrade, Order, Trade
from freqtrade.wallets import Wallets


def at(day, hour=15, month=1):
    return datetime(2025, month, day, hour, tzinfo=UTC)


def contract(market="XNYS"):
    days = [date(2025, 1, d) for d in [2, 3, 6, 7, 8, 9, 10]]
    us = market == "XNYS"
    sessions = tuple(
        CashSession(
            d,
            at(d.day, 14 if us else 1),
            at(d.day, 21 if us else 8),
            None if us else at(d.day, 4),
            None if us else at(d.day, 5),
        )
        for d in days
    )
    return EquityCashContract(
        market,
        "USD" if us else "HKD",
        days[0],
        days[-1],
        sessions,
        tuple(SettlementDay(d, at(d.day, 22 if us else 10)) for d in days),
        "a" * 64,
    )


def config(currency="USD"):
    return dict(
        dry_run=True,
        runmode=RunMode.BACKTEST,
        dry_run_wallet=1000.0,
        trading_mode=TradingMode.SPOT,
        stake_currency=currency,
        tradable_balance_ratio=1.0,
        stake_amount=600.0,
        amend_last_stake_amount=False,
    )


def exchange(currency="USD"):
    return SimpleNamespace(
        get_proxy_coin=lambda: currency, get_pair_base_currency=lambda pair: pair.split("/")[0]
    )


@pytest.fixture(autouse=True)
def isolated_native_state():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, "commit"):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def wallet(c=None, cfg=None):
    c = c or contract()
    return EquitySettlementWallets(
        cfg or config(c.currency),
        exchange(c.currency),
        contract=c,
        asof=c.sessions[0].opens,
        entry_fee_rate=0.01,
    )


def account(w):
    a = object.__new__(Backtesting)
    a.trading_mode, a.margin_mode = TradingMode.SPOT, MarginMode.NONE
    a.config, a.wallets = config(w.contract.currency), w
    a.strategy = SimpleNamespace(order_filled=lambda **kwargs: None)
    a.exchange = exchange(w.contract.currency)
    a._call_adjust_stop = lambda *args: None
    a.run_protections = lambda *args: None
    return a


def trade(pair="ABC/USD", when=None):
    t = LocalTrade(
        id=len(Trade.get_trades_proxy()) + 1,
        pair=pair,
        is_short=False,
        amount=0.0,
        open_rate=100.0,
        stake_amount=0.0,
        open_date=when or at(2),
        is_open=True,
        leverage=1.0,
        trading_mode=TradingMode.SPOT,
        fee_open=0.01,
        fee_close=0.01,
        amount_precision=1.0,
        price_precision=0.01,
        precision_mode=TICK_SIZE,
        precision_mode_price=TICK_SIZE,
        contract_size=1.0,
        orders=[],
    )
    LocalTrade.add_bt_trade(t)
    return t


def fill(a, t, when, qty, price, entry=True):
    a.wallets.advance(when)
    side = t.entry_side if entry else t.exit_side
    o = Order(
        order_id=f"{t.id}-{len(t.orders)}",
        ft_pair=t.pair,
        ft_order_side=side,
        order_type="market",
        side=side,
        price=price,
        ft_price=price,
        average=price,
        amount=qty,
        ft_amount=qty,
        filled=0.0,
        remaining=qty,
        status="open",
        ft_is_open=True,
        order_date=when,
    )
    o._trade_bt = t
    t.orders.append(o)
    row = [when, price, price + 1, price - 1, price]
    with patch("freqtrade.optimize.backtesting.update_liquidation_prices"):
        if entry:
            assert a._try_close_open_order(o, t, when, row)
            t.update_trade(o)
            a.wallets.update()
        else:
            a._process_exit_order(o, t, when, row, t.pair)
    return o


def test_native_partial_full_exit_cash_and_reinvestment():
    w = wallet()
    a, t = account(w), trade()
    assert w.get_available_stake_amount() == pytest.approx(1000 / 1.01)
    fill(a, t, at(2), 5, 100)
    assert (t.amount, t.stake_amount) == (5, 500)
    assert w.get_free("USD") == pytest.approx(495)
    fill(a, t, at(3), 2, 110, False)
    assert (t.amount, t.stake_amount) == (3, 300)
    assert w.get_free("USD") == pytest.approx(495)
    assert w.get_used("USD") == pytest.approx(217.8)
    assert w.get_total("USD") == pytest.approx(712.8)
    assert w.cash_restriction.open_entry_fee_reserve == 3
    with pytest.raises(DependencyException):
        w.get_trade_stake_amount("XYZ/USD", 4)
    # Next trading day OPEN still precedes the explicitly modeled cash release.
    w.advance(at(6))
    assert w.get_free("USD") == pytest.approx(495)
    second = trade("XYZ/USD", at(6))
    fill(a, second, at(6), 1, 50)
    fill(a, t, at(6, 16), 3, 90, False)
    assert not t.is_open
    assert LocalTrade.bt_total_profit == pytest.approx(-19.9)
    assert w.get_free("USD") == pytest.approx(444.5)
    assert w.get_total("USD") == pytest.approx(929.6)
    assert w.get_used("USD") == pytest.approx(485.1)
    w.advance(at(6, 22))
    assert w.get_free("USD") == pytest.approx(662.3)
    w.advance(at(7, 22))
    assert w.get_free("USD") == pytest.approx(929.6)
    assert w.get_used("USD") == 0
    for _ in range(3):
        w.update()
        assert w.get_free("USD") == pytest.approx(929.6)


def test_hk_two_separate_settlement_days():
    c = contract("XHKG")
    w = wallet(c)
    a, t = account(w), trade("0005/HKD", at(2, 2))
    fill(a, t, at(2, 2), 5, 100)
    fill(a, t, at(3, 2), 5, 110, False)
    assert w.get_free("HKD") == pytest.approx(495)
    w.advance(at(6, 10))
    assert w.get_free("HKD") == pytest.approx(495)
    w.advance(at(7, 10) - timedelta(microseconds=1))
    assert w.get_free("HKD") == pytest.approx(495)
    w.advance(at(7, 10))
    assert w.get_free("HKD") == pytest.approx(1039.5)


def test_native_dca_remaining_buy_fee_reserve():
    w = wallet()
    a, t = account(w), trade()
    fill(a, t, at(2), 2, 100)
    fill(a, t, at(3), 3, 110)
    assert t.stake_amount == 530
    assert w.get_free("USD") == pytest.approx(464.7)
    fill(a, t, at(6), 2, 120, False)
    assert w.cash_restriction.open_entry_fee_reserve == pytest.approx(3.18)
    assert w.get_free("USD") == pytest.approx(464.7)


def test_native_rounded_dca_basis_cash_reconciles():
    w = wallet()
    a, t = account(w), trade()
    fill(a, t, at(2), 2, 100.01)
    fill(a, t, at(3), 3, 110.02)
    # The rounded display entry price does not replace the native filled-order
    # basis. Actual cash must still reconcile after the partial realization.
    fill(a, t, at(6), 2, 120, False)
    assert w.get_total("USD") == pytest.approx(702.2192)
    assert w.get_free("USD") == pytest.approx(464.6192)
    assert w.cash_restriction.open_entry_fee_reserve == pytest.approx(3.18048)


def test_settlement_holiday_is_not_a_trading_holiday():
    c = contract()
    # Explicitly omit Jan 6 only from settlement days; exchange still trades.
    c = replace(c, settlement_days=tuple(d for d in c.settlement_days if d.label.day != 6))
    assert c.release_for_fill(at(3)).label == date(2025, 1, 7)
    assert c.release_for_fill(at(6)).label == date(2025, 1, 7)


@pytest.mark.parametrize("when", [at(2, 0), at(2, 22), at(4), at(1)])
def test_reject_non_session_fills(when):
    with pytest.raises(EquityAdmissionError):
        contract().release_for_fill(when)


def test_hk_lunch_and_half_day_reject():
    c = contract("XHKG")
    with pytest.raises(EquityAdmissionError, match="lunch"):
        c.release_for_fill(at(3, 4))
    half = replace(c.sessions[1], closes=at(3, 4), break_start=None, break_end=None)
    c = replace(c, sessions=(c.sessions[0], half, *c.sessions[2:]))
    with pytest.raises(EquityAdmissionError):
        c.release_for_fill(at(3, 5))


@pytest.mark.parametrize(
    "change",
    [
        dict(market="XNYS", currency="HKD"),
        dict(effective_start=date(2024, 1, 1)),
        dict(settlement_days=()),
        dict(source_sha256="unbound"),
    ],
)
def test_contract_rejects_unbound_regime_or_currency(change):
    with pytest.raises(EquityAdmissionError):
        replace(contract(), **change)


def test_reject_tail_and_naive_clock():
    c = contract()
    with pytest.raises(EquityAdmissionError, match="tail"):
        c.release_for_fill(at(10))
    with pytest.raises(EquityAdmissionError, match="timezone"):
        c.release_for_fill(at(2).replace(tzinfo=None))


@pytest.mark.parametrize(
    "change",
    [
        dict(dry_run=False),
        dict(runmode=RunMode.DRY_RUN),
        dict(trading_mode=TradingMode.FUTURES),
        dict(stake_currency="HKD"),
    ],
)
def test_no_live_or_service_entry(change):
    cfg = config() | change
    with pytest.raises(EquityAdmissionError, match="BACKTEST"):
        wallet(cfg=cfg)


def test_reject_overspend_without_replacing_previous_wallet():
    w = wallet()
    w.advance(at(2))
    a, t = account(w), trade()
    w.update()
    before = deepcopy(w._wallets)
    with pytest.raises(EquityAdmissionError, match="overspent"):
        fill(a, t, at(2), 10, 100)
    assert w._wallets == before


def test_mutated_config_never_calls_live_exchange():
    w = wallet()
    w._config["dry_run"] = False
    with patch.object(Wallets, "_update_live") as live:
        with pytest.raises(EquityAdmissionError, match="BACKTEST"):
            w.update()
        live.assert_not_called()


@pytest.mark.parametrize("capital", [{"USD": 1000, "ABC": 5}, float("nan"), -10])
def test_reject_unknown_initial_holdings_or_invalid_capital(capital):
    with pytest.raises(EquityAdmissionError):
        wallet(cfg=config() | {"dry_run_wallet": capital})


def test_pending_native_order_is_not_treated_as_a_filled_cash_flow():
    w = wallet()
    w.advance(at(2))
    t = trade()
    t.orders.append(
        Order(
            order_id="pending",
            ft_pair=t.pair,
            ft_order_side="buy",
            filled=0.0,
            ft_is_open=True,
            amount=1.0,
            ft_amount=1.0,
            ft_price=100.0,
        )
    )
    with pytest.raises(EquityAdmissionError, match="fill boundaries"):
        w.update()


@pytest.mark.parametrize("fault", ["future", "duplicate", "base_fee", "short", "leverage", "fee"])
def test_reject_corrupt_or_unsupported_native_state(fault):
    w = wallet()
    a, t = account(w), trade()
    o = fill(a, t, at(2), 2, 100)
    before = deepcopy(w._wallets)
    if fault == "future":
        o.order_filled_date = at(3)
    elif fault == "duplicate":
        t.orders.append(o)
    elif fault == "base_fee":
        o.ft_fee_base = 1
    elif fault == "short":
        t.is_short = True
    elif fault == "leverage":
        t.leverage = 2
    else:
        t.fee_open = 0.02
    with pytest.raises(EquityAdmissionError):
        w.update()
    assert w._wallets == before


def test_clock_monotonic_and_original_wallet_unchanged():
    w = wallet()
    with pytest.raises(EquityAdmissionError, match="backwards"):
        w.advance(at(1))
    a, t = account(w), trade()
    fill(a, t, at(2), 5, 100)
    native = Wallets(config(), exchange(), is_backtest=True)
    assert native.get_free("USD") == 500
    assert w.get_free("USD") == 495


def test_full_equity_entry_still_rejects_before_native_initialization():
    with patch.object(Backtesting, "__init__") as native:
        with pytest.raises(EquityAdmissionError, match="not integrated"):
            EquityBacktesting(config())
        native.assert_not_called()
