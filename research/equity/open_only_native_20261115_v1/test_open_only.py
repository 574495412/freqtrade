"""Native opening-loop guards and native unset-placement-field compatibility."""
from unittest.mock import patch

import pandas as pd
import pytest
from bootstrap import boot

boot()
from test_sessions import routed
from test_fees import pending_order, probe, zero_trade
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import ExitCheckTuple, ExitType, MarginMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_open_only import EquityOpenOnlyMixin
from freqtrade.persistence import LocalTrade, Trade


class GuardProbe(EquityOpenOnlyMixin, Backtesting):
    pass


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, 'commit'):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def model():
    a, pair, source = routed()
    # Keep the existing real routing/fee MRO, insert the new supported override.
    a.__class__ = type('RoutedOpen', (EquityOpenOnlyMixin, type(a)), {})
    a.fee, a._position_stacking, a.enable_protections = 0., False, False
    a.config['equity_execution_model'] = 'completed_close_next_open_v1'
    a.strategy.order_types = dict(a.strategy.order_types, entry='market', exit='market', stoploss_on_exchange=False)
    row = a._get_ohlcv_as_lists({pair: source})[pair][0]
    return a, pair, row, a.equity_session_plan.opens[0]


@pytest.mark.parametrize('fault', ['model', 'fee', 'roi', 'trailing', 'custom_stop', 'adjust', 'signal', 'profit', 'limit_entry', 'limit_exit', 'exchange_stop', 'stack', 'protection', 'custom_exit'])
def test_unsupported_models_reject_before_native_loop(fault):
    a, pair, row, now = model()
    if fault == 'model': a.config.pop('equity_execution_model')
    elif fault == 'fee': a.fee = .001
    elif fault == 'roi': a.strategy.minimal_roi = {0: .1}
    elif fault == 'trailing': a.strategy.trailing_stop = True
    elif fault == 'custom_stop': a.strategy.use_custom_stoploss = True
    elif fault == 'adjust': a.strategy.position_adjustment_enable = True
    elif fault == 'signal': a.strategy.use_exit_signal = False
    elif fault == 'profit': a.strategy.exit_profit_only = True
    elif fault == 'limit_entry': a.strategy.order_types['entry'] = 'limit'
    elif fault == 'limit_exit': a.strategy.order_types['exit'] = 'limit'
    elif fault == 'exchange_stop': a.strategy.order_types['stoploss_on_exchange'] = True
    elif fault == 'stack': a._position_stacking = True
    elif fault == 'protection': a.enable_protections = True
    elif fault == 'custom_exit':
        a.strategy.__class__ = type('CustomExit', (type(a.strategy),), {'custom_exit': lambda *args, **kwargs: 'intrabar'})
    with patch.object(Backtesting, 'backtest_loop') as native:
        with pytest.raises(EquityAdmissionError): a.backtest_loop(row, pair, now, 'long', True)
        native.assert_not_called()
    assert not Trade.get_trades_proxy()


def test_only_execution_row_loses_future_extrema_source_stays_intact():
    a, pair, row, now = model()
    original = list(row)
    row[2:5] = [10000., .01, 123.]
    with patch.object(Backtesting, 'backtest_loop', return_value=None) as native:
        a.backtest_loop(row, pair, now, 'long', True)
        point = native.call_args.args[0]
        assert point[1:5] == [100., 100., 100., 100.]
    assert row[2:5] == [10000., .01, 123.]
    assert a._equity_open_context is None
    assert original[2:5] != point[2:5]


@pytest.mark.parametrize('exit_type', [ExitType.ROI, ExitType.STOP_LOSS, ExitType.CUSTOM_EXIT, ExitType.PARTIAL_EXIT])
def test_unsupported_exits_reject_before_native_close_date_mutation(exit_type):
    a, pair, row, now = model()
    row[0] = pd.Timestamp(now)
    row[2:5] = [100.] * 3
    t = zero_trade(pair, now)
    a._equity_open_context = (pair, now, 100.)
    with pytest.raises(EquityAdmissionError):
        a._get_exit_for_signal(t, row, ExitCheckTuple(exit_type), now)
    assert t.close_date is None and not t.orders


def test_order_creation_outside_loop_rejects_without_trade():
    a, pair, row, now = model()
    row[0] = pd.Timestamp(now)
    with pytest.raises(EquityAdmissionError): a._enter_trade(pair, row, 'long')
    assert not Trade.get_trades_proxy()


@pytest.mark.parametrize('mirror', [None, 1., 2.])
def test_native_missing_placement_mirror_accepts_only_actual_valid_amount(mirror):
    a = probe()
    a.exchange.margin_mode = MarginMode.NONE
    a.exchange.get_liquidation_price = lambda **kwargs: None
    now = a.wallets.asof
    t = zero_trade(when=now)
    o = pending_order(t, 1)
    o.ft_amount = mirror
    o.order_date = now
    if mirror == 2.:
        with pytest.raises(EquityAdmissionError): a._try_close_open_order(o, t, now, [now, 100., 100., 100., 100.])
        assert not o.safe_filled
    else:
        assert a._try_close_open_order(o, t, now, [now, 100., 100., 100., 100.])
        assert o.safe_filled == 1.
