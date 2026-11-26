"""Post-run validation of pre-split claims; no additional financial starts."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import test_split as native_unit
from test_sessions import routed

from freqtrade.data.equity_dividends import CashDividend
from freqtrade.data.equity_splits import ImplementedShareSplit, ShareSplitContract
from freqtrade.equity_split_wallets import EquitySplitWallets
from freqtrade.persistence import LocalTrade, Trade


def setup_wallet(market, new=2, old=1, index=5, has_dividend=True):
    account, pair, _ = routed(market)
    cash = account.wallets.contract
    start, later = cash.sessions[0].opens, cash.sessions[index].opens
    splits = ShareSplitContract(
        cash,
        (pair,),
        start,
        cash.sessions[-1].closes,
        (ImplementedShareSplit("later", pair, later, start, new, old, "a" * 64),),
        "b" * 64,
    )
    dividend = CashDividend(
        "pre-split-claim",
        pair,
        cash.sessions[1].opens,
        start - timedelta(days=1),
        Decimal(1),
        Decimal(0),
        start - timedelta(days=1),
        "c" * 64,
        "c" * 64,
    )
    dividends = replace(account.wallets.dividends, events=(dividend,) if has_dividend else ())
    wallet = EquitySplitWallets(
        account.wallets._config,
        account.exchange,
        fees=account.wallets.fees,
        dividends=dividends,
        splits=splits,
        asof=start,
    )
    # The shared constructor helper declares USD. Adapt only the actual
    # fixture's explicitly selected native trade identity/currency here.

    from freqtrade.persistence import Order
    from freqtrade.persistence.equity_trade import EquitySplitTrade

    terms = native_unit.kwargs(splits)
    terms.update(pair=pair, stake_currency=cash.currency)
    trade = EquitySplitTrade(splits=splits, **terms)
    trade.orders.append(
        Order(
            id=1,
            ft_trade_id=1,
            ft_pair=pair,
            order_id="1",
            symbol=pair,
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
            order_date=start,
            order_filled_date=start,
        )
    )
    trade.recalc_trade_from_orders()
    LocalTrade.add_bt_trade(trade)
    return wallet, trade, cash, splits


@pytest.mark.parametrize("market", ["XNYS", "XHKG"])
def test_later_split_does_not_rescale_an_existing_dividend_claim(market):
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    try:
        wallet, trade, cash, splits = setup_wallet(market)
        later = splits.events[0].effective_open
        with patch.object(Trade, "commit"):
            wallet.advance(cash.sessions[1].opens)
            assert wallet.dividend_receivable == 6 and trade.amount == 6
            wallet.advance(later)
            assert trade.amount == 12 and trade.stake_amount == 600
            assert wallet.dividend_receivable == 6 and wallet.paid_dividends == 0
            assert wallet.dividend_journal[0].quantity == 6
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = old


@pytest.mark.parametrize("market", ["XNYS", "XHKG"])
def test_fractional_wallet_failure_restores_cash_and_native_position(market):
    from freqtrade.data.history.equity import EquityAdmissionError

    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    try:
        wallet, trade, cash, splits = setup_wallet(market, 1, 4, 1, False)
        wallet.advance(cash.sessions[0].closes)
        original_cash = (
            wallet.get_free(cash.currency),
            wallet.get_used(cash.currency),
            wallet.get_total(cash.currency),
            wallet.cash_restriction,
        )
        original_trade = (trade.amount, trade.stake_amount, trade.unit_asof, trade.unit_journal)
        with pytest.raises(EquityAdmissionError, match="Fractional"):
            wallet.advance(splits.events[0].effective_open)
        assert wallet.asof == cash.sessions[0].closes
        assert original_cash == (
            wallet.get_free(cash.currency),
            wallet.get_used(cash.currency),
            wallet.get_total(cash.currency),
            wallet.cash_restriction,
        )
        assert original_trade == (
            trade.amount,
            trade.stake_amount,
            trade.unit_asof,
            trade.unit_journal,
        )
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = old
