"""Native fills/funding and causal risk inputs, without a substitute account."""
import ast
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, '/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sys.path.insert(0, str(HERE))
from NativeAllocations import marked_budget, open_ledger_value
from RiskWeights import plan_weights
from TSMomPortfolio import CovarianceRiskTS
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade, Order, Trade


def clock(day, hour):
    return datetime(2025, 1, day, hour, tzinfo=UTC)


class FundingOnlyExchange(Exchange):
    def close(self):
        pass  # No initialized transports in this native funding arithmetic fixture.


def fixture(short):
    account = object.__new__(Backtesting)
    account.trading_mode = TradingMode.FUTURES
    account.margin_mode = MarginMode.CROSS
    account.funding_fee_timeframe_secs = 3600
    account.exchange = object.__new__(FundingOnlyExchange)
    account.strategy = SimpleNamespace(order_filled=lambda **kwargs: None)
    account.wallets = None
    account.config = {'stake_currency': 'USDT'}
    account._call_adjust_stop = lambda *args: None
    pair = 'BTC/USDT:USDT'
    account.futures_data = {pair: Exchange._add_funding_columns(pd.DataFrame({
        'date': [clock(1, 8), clock(2, 8), clock(3, 0), clock(3, 8)],
        'open_fund': [.01, -.02, .005, .01], 'open_mark': [100.] * 4}))}
    trade = LocalTrade(id=1, pair=pair, is_short=short, amount=0., open_rate=100.1,
                       stake_amount=0., open_date=clock(1, 1), is_open=True,
                       leverage=3., trading_mode=TradingMode.FUTURES,
                       fee_open=.0015, fee_close=.0015, amount_precision=.001,
                       price_precision=.1, precision_mode=TICK_SIZE,
                       precision_mode_price=TICK_SIZE, contract_size=1., orders=[])
    return account, trade


def fill(account, trade, when, amount, price, entry):
    # Native _check_trade_exit accrues funding before quantity adjustment.
    account._run_funding_fees(trade, when)
    side = trade.entry_side if entry else trade.exit_side
    order = Order(id=len(trade.orders)+1, ft_pair=trade.pair, ft_order_side=side,
                  order_type='market', side=side, price=price, ft_price=price,
                  average=price, amount=amount, filled=0., remaining=amount,
                  status='open', ft_is_open=True, order_date=when)
    order._trade_bt = trade
    trade.orders.append(order)
    with patch('freqtrade.optimize.backtesting.update_liquidation_prices'), patch.object(Trade, 'commit'):
        assert account._try_close_open_order(order, trade, when, [when, price, price+1, price-1, price])
        trade.update_trade(order)
    return order


@pytest.mark.parametrize('short', [False, True])
def test_actual_native_dca_partial_exit_funding_and_cash(short):
    account, trade = fixture(short)
    sign = -1 if short else 1
    fill(account, trade, clock(1, 1), 2., 100.1, True)
    account._run_funding_fees(trade, clock(1, 8))
    assert trade.funding_fees == -2 * sign
    fill(account, trade, clock(2, 1), 1., 105.2, True)
    assert trade.amount == 3.
    account._run_funding_fees(trade, clock(2, 8))
    assert trade.funding_fees == 4 * sign
    fill(account, trade, clock(3, 1), .8, 110.3, False)
    assert trade.amount == 2.2
    assert trade.funding_fees == 2.5 * sign
    # Entry cash305.4, partial exit88.24, remaining2.2 at111.7; all fees
    # and historical funding included once, including the partial realization.
    expected = sign * 28.58 - .95907 + sign * 2.5
    assert open_ledger_value(trade, 111.7) == pytest.approx(expected, abs=1e-10)
    assert marked_budget(1000., 10., [trade], {trade.pair: 111.7}) == pytest.approx(1010+expected)
    realized_funding = math.fsum(o.funding_fee or 0. for o in trade.orders)
    rounding = sign*(trade.stake_amount*3-trade.amount*trade.open_rate)*(1+sign*trade.fee_open)
    native = trade.calculate_profit(111.7).total_profit - realized_funding - rounding
    assert native == pytest.approx(expected, abs=1e-10)
    account._run_funding_fees(trade, clock(3, 8))
    assert trade.funding_fees == pytest.approx(.3*sign)
    for _ in range(3):
        account._run_funding_fees(trade, clock(3, 9), force=True)
        assert trade.funding_fees == pytest.approx(.3*sign)
    fill(account, trade, clock(4, 1), 2.2, 115.4, False)
    assert not trade.is_open
    assert trade.close_profit_abs == pytest.approx(sign*36.72-.97128+sign*.3, abs=1e-10)
    assert sum(o.funding_fee or 0. for o in trade.orders) == pytest.approx(.3*sign)


def test_exact_author_ast():
    source = ast.parse((HERE/'source/core/tsmom_engine.py').read_text())
    original = {n.name: n for cls in source.body if isinstance(cls, ast.ClassDef)
                for n in cls.body if isinstance(n, ast.FunctionDef)}
    local = ast.parse((HERE/'AuthorMath.py').read_text())
    for cls in local.body:
        if isinstance(cls, ast.ClassDef):
            for method in cls.body:
                if isinstance(method, ast.FunctionDef):
                    assert ast.dump(method, include_attributes=False) == ast.dump(original[method.name], include_attributes=False)


@pytest.mark.parametrize('mode', ['covariance', 'diagonal', 'gross_one'])
def test_source_lag_and_actual_future_perturbation(mode):
    days = pd.date_range('2023-10-31', '2026-08-31', tz='UTC', freq='D')
    rng = np.random.default_rng(218)
    close = pd.DataFrame(100*np.exp(np.cumsum(rng.normal(.0005, .02, (len(days), 4)), axis=0)), index=days)
    decisions = pd.date_range('2025-01-01', '2026-08-31', tz='UTC', freq='D')
    baseline = plan_weights(close, decisions, mode)[0]
    for cutoff in [pd.Timestamp('2025-06-01', tz='UTC'), pd.Timestamp('2026-02-01', tz='UTC')]:
        changed = close.copy()
        changed.loc[changed.index >= cutoff-pd.Timedelta(days=1)] *= 7
        actual = plan_weights(changed, decisions, mode)[0]
        pd.testing.assert_frame_equal(actual.loc[:cutoff], baseline.loc[:cutoff], check_exact=True)
        truncated = close.loc[:cutoff-pd.Timedelta(days=2)]
        prefix = plan_weights(truncated, decisions[decisions <= cutoff], mode)[0]
        pd.testing.assert_frame_equal(prefix, baseline.loc[:cutoff], check_exact=True)
    assert baseline.abs().le(.5+1e-12).all().all()
    assert baseline.abs().sum(axis=1).le(3+1e-12).all()


@pytest.mark.parametrize('kind', ['gap', 'nonfinite', 'duplicate'])
def test_no_missing_price_imputation(kind):
    dates = pd.date_range('2023-10-31', '2025-01-01', tz='UTC', freq='D')
    close = pd.DataFrame({'a': np.arange(len(dates))+100.}, index=dates)
    if kind == 'gap':
        close = close.drop(close.index[10])
    elif kind == 'nonfinite':
        close.iloc[10, 0] = np.nan
    else:
        close = pd.concat([close, close.iloc[[-1]]])
    with pytest.raises(ValueError):
        plan_weights(close, pd.date_range('2025-01-01', periods=1, tz='UTC'), 'covariance')


def test_partial_adjustment_uses_native_stake_ratio_and_runs_once():
    owner = CovarianceRiskTS({})
    account, trade = fixture(False)
    fill(account, trade, clock(1, 1), 2., 100.1, True)
    day = pd.Timestamp('2025-01-02', tz='UTC')
    context = {'clock': day+pd.Timedelta(hours=1), 'quantities': {trade.pair: 1.2},
               'minimums': {trade.pair: 1.}}
    owner.context = lambda _: context
    owner._attempted = set()
    owner.research_weights = pd.DataFrame({trade.pair: [.1]}, index=[day])
    owner.research_trace = {'adjustments': []}
    stake, tag = owner.adjust_trade_position(trade, clock(2, 1), 110., .1, 1., 100.)
    expected_exit_quantity = abs(stake)*trade.amount/trade.stake_amount
    assert expected_exit_quantity == pytest.approx(.8)
    assert tag == 'source_daily_quantity'
    assert owner.adjust_trade_position(trade, clock(2, 1), 110., .1, 1., 100.) is None


def test_leverage_is_frozen_and_rejects_unavailable_margin():
    owner = CovarianceRiskTS({})
    args = ('BTC/USDT:USDT', clock(1, 1), 100., 1.)
    assert owner.leverage(*args, 20., None, 'long') == 3.
    with pytest.raises(ValueError):
        owner.leverage(*args, 2., None, 'long')
