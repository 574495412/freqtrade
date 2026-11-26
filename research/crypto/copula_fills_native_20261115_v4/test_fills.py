"""Failure admission and native callback fallback checks, not profit evidence."""
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[2].parent / 'CZ-Agent/vendor/freqtrade'
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(HERE.parent / 'copula_execution_native_20261115_v3'))

from CopulaPortfolio import CopulaPortfolio
from audit_fixtures import canonical_stats, cash, funding_coverage, quantity_before
from freqtrade.persistence import Trade
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper


def strategy():
    s = object.__new__(CopulaPortfolio)
    s._fatal_error = None
    s.research_trace = {'errors': [], 'entry_confirmations': []}
    s._context = {'action': 'entry', 'at': pd.Timestamp('2025-01-02T00:05Z'),
                  'targets': {}, 'rates': {}, 'admission': {'requested_stakes': {}}}
    return s


def test_source20_financial_guard_stays_closed_before_initialization():
    s = strategy()
    with patch.object(s, 'bot_start') as start:
        with pytest.raises(ValueError, match='admission remains incomplete'):
            s.ft_bot_start()
        start.assert_not_called()


def test_stake_callback_failure_returns_zero_under_actual_native_wrapper():
    s = strategy()
    value = strategy_safe_wrapper(s.custom_stake_amount, default_retval=999.)(
        pair='REAL/USDT:USDT', current_time=s._context['at'], current_rate=1.,
        proposed_stake=999., min_stake=1., max_stake=1000., leverage=3.,
        entry_tag='fixture', side='long')
    assert value == 0 and s._fatal_error['stage'] == 'custom_stake_amount'


def test_entry_callback_failure_returns_false_under_actual_native_wrapper():
    s = strategy()
    value = strategy_safe_wrapper(s.confirm_trade_entry, default_retval=True)(
        pair='REAL/USDT:USDT', order_type='market', amount=10., rate=1.,
        time_in_force='GTC', current_time=s._context['at'], entry_tag='fixture', side='long')
    assert value is False and s._fatal_error['stage'] == 'confirm_trade_entry'


@pytest.mark.parametrize('fault', ['side', 'quantity', 'rate', 'market'])
def test_native_entry_cannot_change_frozen_pair_contract(fault):
    s = strategy()
    pair, at = 'FAKEONE/USDT:USDT', s._context['at']
    s._context['targets'][pair] = {'quantity': 4.9, 'side': 'short'}
    s._context['rates'][pair] = 100.
    params = dict(pair=pair, order_type='market', amount=4.9, rate=100.,
                  time_in_force='GTC', current_time=at, entry_tag='fixture', side='short')
    params[{'side': 'side', 'quantity': 'amount', 'rate': 'rate', 'market': 'order_type'}[fault]] = {'side': 'long', 'quantity': 4.8, 'rate': 101., 'market': 'limit'}[fault]
    assert s.confirm_trade_entry(**params) is False
    assert s._fatal_error and not s.research_trace['entry_confirmations']


@pytest.mark.parametrize('reason', ['exit_signal', 'copula_neutral_or_week_boundary'])
def test_actual_native_signal_tag_accepts_full_paired_exit(reason):
    s = strategy()
    pair = 'FAKEONE/USDT:USDT'
    s._context.update(action='exit', previous={pair: {'quantity': 4.9, 'side': 'short'}})
    assert s.confirm_trade_exit(pair=pair, trade=None, order_type='market', amount=4.9,
        rate=100., time_in_force='GTC', exit_reason=reason, current_time=s._context['at'])
    assert s._fatal_error is None


@pytest.mark.parametrize('reason', ['stop_loss', 'trailing_stop_loss', 'roi', 'liquidation'])
def test_unadmitted_risk_exit_invalidates_instead_of_becoming_source_profit(reason):
    s = strategy()
    assert s.confirm_trade_exit(pair='FAKEONE/USDT:USDT', trade=None, order_type='market', amount=4.9,
        rate=100., time_in_force='GTC', exit_reason=reason, current_time=s._context['at']) is False
    assert s._fatal_error['stage'] == 'confirm_trade_exit'


def test_actual_duplicate_or_pending_native_legs_reject():
    s = strategy()
    leg = SimpleNamespace(pair='FAKEONE/USDT:USDT', has_open_orders=False, amount=4.9, trade_direction='short')
    with patch.object(Trade, 'get_trades_proxy', return_value=[leg, leg]):
        with pytest.raises(ValueError, match='Duplicate'):
            s._actual()
    leg.has_open_orders = True
    with patch.object(Trade, 'get_trades_proxy', return_value=[leg]):
        with pytest.raises(ValueError, match='pending'):
            s._actual()


def order(at, entry, quantity=2.):
    return {'order_filled_timestamp': int(pd.Timestamp(at).timestamp() * 1000),
            'ft_is_entry': entry, 'amount': quantity, 'safe_price': 100. if entry else 110.}


@pytest.mark.parametrize('opened,closed,expected', [
    ('2025-01-02T00:05Z', '2025-01-02T02:05Z', True),
    ('2025-01-02T01:05Z', '2025-01-02T02:05Z', False),
    ('2025-01-02T00:05Z', '2025-01-02T00:55Z', False),
    ('2025-01-02T00:05Z', '2025-01-02T01:00Z', True),
])
def test_missing_event_uses_actual_pre_event_quantity(opened, closed, expected):
    at = pd.Timestamp('2025-01-02T01:00Z')
    t = {'pair': 'FAKEONE/USDT:USDT', 'orders': [order(opened, True), order(closed, False)]}
    expected_grid = pd.DatetimeIndex([at])
    rows = funding_coverage([t], [t['pair']], expected_grid,
        {t['pair']: pd.DataFrame(index=pd.DatetimeIndex([], tz='UTC'))})
    assert rows[0]['exposed'] is expected and rows[0]['observed_rate'] is None
    assert (quantity_before([t], t['pair'], at) > 0) is expected


@pytest.mark.parametrize('short,expected', [(False, D('19.3274')), (True, D('-20.5874'))])
def test_independent_cash_funding_and_current_event_boundary(short, expected):
    t = dict(pair='FAKEONE/USDT:USDT', is_short=short, fee_open=.0015, fee_close=.0015,
             orders=[order('2025-01-02T00:05Z', True), order('2025-01-02T02:05Z', False)])
    funds = pd.DataFrame({'rate': [.0001, .0001], 'mark': [105., 108.]},
        index=pd.DatetimeIndex(['2025-01-02T01:00Z', '2025-01-02T02:00Z']))
    profit, funding = cash(t, funds)
    assert profit == expected
    assert funding == (1 if short else -1) * D('.0426')
    assert cash(t, funds, at=pd.Timestamp('2025-01-02T02:00Z')) == (1 if short else -1) * D('.021')


def test_serialization_normalization_does_not_hide_a_balance_change_or_tag():
    a = {'drawdown_start': '1970-01-01 00:00:00+00:00', 'wallet_stats': {'start_balance': '1000'},
         'enter_tag': '2025-01-01 00:00:00'}
    b = deepcopy(a)
    b['wallet_stats']['start_balance'] = 1000
    b['drawdown_start'] = '1970-01-01T00:00:00+00:00'
    assert canonical_stats(a) == canonical_stats(b)
    b['wallet_stats']['start_balance'] = 1001
    assert canonical_stats(a) != canonical_stats(b)
    assert canonical_stats(a)['enter_tag'] == a['enter_tag']
