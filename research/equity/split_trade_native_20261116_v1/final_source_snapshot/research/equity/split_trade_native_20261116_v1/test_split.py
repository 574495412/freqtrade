"""Unit fixtures are constructed explicitly; native start evidence is separate."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from bootstrap import boot


boot()
from test_sessions import routed

from freqtrade.data.equity_splits import ImplementedShareSplit, ShareSplitContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import TradingMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade, Order, Trade
from freqtrade.persistence.equity_trade import EquitySplitTrade


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, "commit"):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def split_contract(new=2, old=1, index=1):
    account, pair, _ = routed()
    cash = account.wallets.contract
    at = cash.sessions[index].opens
    event = ImplementedShareSplit(
        "fixture-split", pair, at, at - timedelta(days=1), new, old, "a" * 64
    )
    return ShareSplitContract(
        cash, (pair,), cash.sessions[0].opens, cash.sessions[-1].closes, (event,), "b" * 64
    )


def kwargs(contract):
    return {
        "id": 1,
        "pair": "ABC/USD",
        "base_currency": "ABC",
        "stake_currency": "USD",
        "open_date": contract.coverage_start,
        "stake_amount": 600.0,
        "amount": 0.0,
        "open_rate": 100.0,
        "fee_open": 0.0,
        "fee_close": 0.0,
        "leverage": 1.0,
        "trading_mode": TradingMode.SPOT,
        "is_short": False,
        "is_open": True,
        "amount_precision": 1.0,
        "price_precision": 0.01,
        "precision_mode": 4,
        "precision_mode_price": 4,
        "contract_size": 1.0,
        "orders": [],
    }


def entry(contract):
    trade = EquitySplitTrade(splits=contract, **kwargs(contract))
    order = Order(
        id=1,
        ft_trade_id=1,
        ft_pair=trade.pair,
        order_id="1",
        symbol=trade.pair,
        ft_order_side="buy",
        side="buy",
        order_type="market",
        status="closed",
        ft_is_open=False,
        amount=6.0,
        filled=6.0,
        remaining=0.0,
        price=100.0,
        average=100.0,
        cost=600.0,
        order_date=contract.coverage_start,
        order_filled_date=contract.coverage_start,
    )
    trade.orders.append(order)
    trade.recalc_trade_from_orders()
    return trade


def test_native_default_factory_still_constructs_original_trade():
    contract = split_contract()
    plain = Backtesting.__new__(Backtesting)._create_trade(**kwargs(contract))
    assert type(plain) is LocalTrade


@pytest.mark.parametrize("new,old", [(0, 1), (1, 0), (-1, 2), (True, 1), (2, 2)])
def test_invalid_exact_terms_reject(new, old):
    with pytest.raises(EquityAdmissionError):
        split_contract(new, old)


@pytest.mark.parametrize("fault", ["late", "nonopen", "duplicate", "currency", "mutable", "sha"])
def test_unadmitted_implementation_contract_rejects(fault):
    c = split_contract()
    with pytest.raises(EquityAdmissionError):
        e = c.events[0]
        if fault == "late":
            replace(e, known_at=e.effective_open + timedelta(seconds=1))
        elif fault == "nonopen":
            replace(c, events=(replace(e, effective_open=e.effective_open + timedelta(minutes=1)),))
        elif fault == "duplicate":
            replace(c, events=(e, e))
        elif fault == "currency":
            replace(c, pairs=("ABC/HKD",))
        elif fault == "mutable":
            replace(c, events=[e])
        else:
            replace(e, source_sha256="bad")


def test_three_for_one_keeps_orders_cash_basis_and_unrounded_average():
    c = split_contract(3, 1)
    t = entry(c)
    original = deepcopy(t.orders[0].to_json("buy", False))
    t.advance_units(c.events[0].effective_open)
    assert t.amount == 18 and t.stake_amount == 600
    assert t.open_rate == pytest.approx(100 / 3, abs=1e-12)
    assert t.open_rate != 33.33
    assert t.open_trade_value == pytest.approx(600.0, abs=1e-10)
    for _ in range(3):
        t.recalc_trade_from_orders()
        assert t.amount == 18 and t.stake_amount == 600
    assert t.orders[0].to_json("buy", False) == original
    assert t.calc_profit(33.34) == pytest.approx(0.12, abs=1e-10)


def test_integral_reverse_split_and_same_open_fill_units():
    c = split_contract(1, 2)
    t = entry(c)
    t.advance_units(c.events[0].effective_open)
    assert t.amount == 3 and t.stake_amount == 600 and t.open_rate == 200
    assert c.quantity(t.pair, Decimal(3), c.events[0].effective_open, c.coverage_end) == 3


def test_fractional_entitlement_rejects_before_mutating_any_trade_state():
    c = split_contract(1, 4)
    t = entry(c)
    before = t.__dict__.copy()
    order = deepcopy(t.orders[0].to_json("buy", False))
    with pytest.raises(EquityAdmissionError, match="Fractional"):
        t.advance_units(c.events[0].effective_open)
    assert t.__dict__ == before
    assert t.orders[0].to_json("buy", False) == order


def test_future_ratio_does_not_change_prior_native_state():
    a, b = split_contract(2, 1, 5), split_contract(999, 1, 5)
    x, y = entry(a), entry(b)
    before = a.cash.sessions[4].closes
    x.advance_units(before)
    y.advance_units(before)
    for key in ["amount", "stake_amount", "open_rate", "open_trade_value", "unit_journal"]:
        assert getattr(x, key) == getattr(y, key)


def test_previously_seen_raw_order_cannot_be_rewritten():
    c = split_contract()
    t = entry(c)
    t.orders[0].price = t.orders[0].average = 99.0
    with pytest.raises(EquityAdmissionError, match="changed"):
        t.advance_units(c.events[0].effective_open)
    assert t.amount == 6 and t.stake_amount == 600


def test_carried_native_order_and_backwards_clock_reject():
    c = split_contract()
    t = entry(c)
    with pytest.raises(EquityAdmissionError, match="coverage"):
        t.advance_units(c.coverage_start - timedelta(seconds=1))
    t.orders[0].ft_is_open = True
    with pytest.raises(EquityAdmissionError, match="Carried"):
        t.advance_units(c.events[0].effective_open)


def test_full_post_split_native_exit_recalculates_profit_with_original_fill_records():
    c = split_contract(2, 1)
    t = entry(c)
    at = c.events[0].effective_open
    t.advance_units(at)
    t.orders.append(
        Order(
            id=2,
            ft_trade_id=1,
            ft_pair=t.pair,
            order_id="2",
            symbol=t.pair,
            ft_order_side="sell",
            side="sell",
            order_type="market",
            status="closed",
            ft_is_open=False,
            amount=12.0,
            filled=12.0,
            remaining=0.0,
            price=51.0,
            average=51.0,
            cost=612.0,
            order_date=at,
            order_filled_date=at,
        )
    )
    t.close_date = at
    t.close(51.0, show_msg=False)
    assert not t.is_open and t.close_profit_abs == 12
    assert [o.safe_filled for o in t.orders] == [6.0, 12.0]
