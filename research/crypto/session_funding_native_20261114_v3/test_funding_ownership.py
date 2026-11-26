"""Native trade/order fixtures and hand-computed event ownership expectations."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, "/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
sys.path.insert(0, str(Path(__file__).parent))

from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exchange import Exchange
from freqtrade.optimize.backtesting import Backtesting, CLOSE_IDX, HIGH_IDX, LOW_IDX
from freqtrade.persistence import LocalTrade, Order

from FundingOwnershipBacktesting import FundingOwnershipBacktesting


def clock(hour):
    return datetime(2025, 1, 1, hour, tzinfo=UTC)


def fixture(mode, short=False, opened=7, amount=2):
    account = object.__new__(FundingOwnershipBacktesting)
    account.funding_ownership = mode
    account._funding_closing_clock = {}
    account.funding_ownership_calls = 0
    account.trading_mode = TradingMode.FUTURES
    account.margin_mode = MarginMode.ISOLATED
    account.funding_fee_timeframe_secs = 3600
    account.strategy = SimpleNamespace(order_filled=lambda **kwargs: None)
    account.futures_data = {
        "BTC/USDT:USDT": Exchange._add_funding_columns(
            pd.DataFrame(
                {
                    "date": [clock(8), clock(16)],
                    "open_fund": [0.01, -0.02],
                    "open_mark": [100.0, 100.0],
                }
            )
        )
    }
    trade = LocalTrade(
        id=1,
        pair="BTC/USDT:USDT",
        is_short=short,
        amount=amount,
        open_rate=100,
        stake_amount=amount * 100,
        open_date=clock(opened),
        leverage=1,
        trading_mode=TradingMode.FUTURES,
        fee_open=0.0,
        fee_close=0.0,
    )
    entry = Order(
        id=1,
        ft_pair=trade.pair,
        ft_order_side=trade.entry_side,
        order_type="market",
        side=trade.entry_side,
        price=100,
        ft_price=100,
        average=100,
        amount=amount,
        filled=amount,
        remaining=0,
        status="closed",
        ft_is_open=False,
        order_filled_date=clock(opened),
        funding_fee=0.0,
    )
    trade.orders = [entry]
    return account, trade


@pytest.mark.parametrize("mode,expected", [("before_orders", -2.0), ("after_orders", 0.0)])
def test_native_full_exit_transfers_final_policy_amount(mode, expected):
    account, trade = fixture(mode)
    # Before knowing whether there is an exit, an existing position accrues
    # the event. Full-exit handling must reverse that provisional amount for
    # AFTER policy before the native order captures it.
    account._run_funding_fees(trade, clock(8))
    assert trade.funding_fees == -2.0
    order = Order(
        id=2,
        ft_pair=trade.pair,
        ft_order_side=trade.exit_side,
        order_type="market",
        side=trade.exit_side,
        price=100,
        ft_price=100,
        average=100,
        amount=2,
        filled=0,
        remaining=2,
        status="open",
        ft_is_open=True,
    )
    trade.orders.append(order)
    row = [None] * (max(CLOSE_IDX, HIGH_IDX, LOW_IDX) + 1)
    row[HIGH_IDX], row[LOW_IDX], row[CLOSE_IDX] = 100, 100, 100
    assert account._try_close_open_order(order, trade, clock(8), row)
    assert order.funding_fee == expected
    assert trade.funding_fees == expected
    assert trade.funding_fee_running == 0
    assert account._funding_closing_clock == {}


@pytest.mark.parametrize("mode,expected", [("before_orders", 0.0), ("after_orders", 3.0)])
def test_successor_open_event_only_owned_in_after_mode(mode, expected):
    account, trade = fixture(mode, short=True, opened=8, amount=3)
    account._run_funding_fees(trade, clock(8), force=True)
    assert trade.funding_fees == expected
    account._run_funding_fees(trade, clock(9), force=True)
    assert trade.funding_fees == expected


@pytest.mark.parametrize("mode", ["before_orders", "after_orders"])
def test_held_trade_has_two_signed_events_without_compounding_fees(mode):
    account, trade = fixture(mode)
    account._run_funding_fees(trade, clock(16))
    # Long pays2 at08 and receives4 at16, cumulative+2. Repeated callbacks
    # are cumulative reconciliation, never fresh cash events.
    assert trade.funding_fees == 2
    for _ in range(3):
        account._run_funding_fees(trade, clock(17), force=True)
    assert trade.funding_fees == 2


def test_default_off_calls_original_native_methods():
    account, trade = fixture("inclusive")
    with patch.object(Backtesting, "_run_funding_fees", return_value="native") as call:
        assert account._run_funding_fees(trade, clock(8), force=True) == "native"
        call.assert_called_once_with(trade, clock(8), force=True)
    with patch.object(Backtesting, "_try_close_open_order", return_value="native") as call:
        assert account._try_close_open_order(None, trade, clock(8), []) == "native"
        call.assert_called_once_with(None, trade, clock(8), [])
