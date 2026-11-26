"""Boundary rejection and native-disk loading; six real starts are audited separately."""
import sys
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pandas as pd
import pytest
from bootstrap import PARENT, boot

boot()
sys.path.append(str(PARENT.parent / 'session_routing_native_20261115_v1'))
from test_sessions import routed, RoutedProbe
from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import CandleType
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_fixture_lifecycle import EquityFixtureLifecycleMixin
from freqtrade.persistence import Trade, LocalTrade


class Probe(EquityFixtureLifecycleMixin, RoutedProbe):
    pass


@pytest.fixture(autouse=True)
def isolation():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def shell(tmp_path):
    a, pair, source = routed()
    a.__class__ = Probe
    a.config = dict(a.config, synthetic_equity_fixture=True, backtest_cache='none',
                    export='trades', exchange={'pair_whitelist':[pair]}, datadir=tmp_path)
    a.dataprovider._config = a.config
    a.dataprovider._equity_raw = {}
    a.strategylist = [a.strategy]
    a.timerange = object()
    a.price_pair_prec = {}
    a.available_pairs = []
    return a, pair, source


@pytest.mark.parametrize('fault', ['flag', 'universe', 'cache', 'strategies', 'startup', 'export', 'repeat'])
def test_fixture_gate_rejects_before_native_start(tmp_path, fault):
    a, pair, source = shell(tmp_path)
    if fault == 'flag': a.config['synthetic_equity_fixture'] = False
    if fault == 'universe': a.config['exchange']['pair_whitelist'] = ['AAPL/USD']
    if fault == 'cache': a.config['backtest_cache'] = 'day'
    if fault == 'strategies': a.strategylist *= 2
    if fault == 'startup': a.required_startup = 1
    if fault == 'export': a.config['export'] = 'none'
    if fault == 'repeat': a._equity_fixture_started = True
    with patch.object(Backtesting, 'start') as native:
        with pytest.raises(EquityAdmissionError): a.start()
        native.assert_not_called()


def test_actual_native_feather_load_is_full_grid_and_defensive(tmp_path):
    a, pair, source = shell(tmp_path)
    FeatherDataHandler(tmp_path).ohlcv_store(pair, '1d', source, CandleType.SPOT)
    data, timerange = a.load_bt_data()
    pd.testing.assert_frame_equal(data[pair], source, check_dtype=False)
    assert timerange is a.timerange and a.available_pairs == [pair]
    saved = a._equity_loaded[pair].copy()
    data[pair].loc[0, 'open'] = 10000
    pd.testing.assert_frame_equal(saved, a._equity_loaded[pair])
    with pytest.raises(EquityAdmissionError, match='injected'): a.load_bt_data()


def test_missing_disk_session_never_becomes_zero_or_fill(tmp_path):
    a, pair, source = shell(tmp_path)
    FeatherDataHandler(tmp_path).ohlcv_store(pair, '1d', source.iloc[:-1], CandleType.SPOT)
    with pytest.raises(EquityAdmissionError, match='Session mismatch'): a.load_bt_data()
    assert not Trade.get_trades_proxy()


@pytest.mark.parametrize('which', ['start', 'end'])
def test_trimmed_midnight_labels_must_cover_original_full_window(tmp_path, which):
    a, pair, source = shell(tmp_path)
    plan = a.equity_session_plan
    start, end = plan.labels[0].to_pydatetime(), plan.labels[-1].to_pydatetime()
    if which == 'start': start += timedelta(days=1)
    else: end -= timedelta(days=1)
    a._require_open_only = lambda: plan
    with patch.object(Backtesting, 'backtest') as native:
        with pytest.raises(EquityAdmissionError, match='complete fixture'): a.backtest({}, start, end)
        native.assert_not_called()


def test_incomplete_native_return_cannot_mark_lifecycle_success(tmp_path):
    a, _, _ = shell(tmp_path)
    a.equity_open_snapshots, a.equity_close_snapshots = [], []
    with patch.object(Backtesting, 'start'):
        with pytest.raises(EquityAdmissionError, match='Missing opening'): a.start()
    assert a.equity_lifecycle_completed is False


def test_historical_entry_still_rejects_before_native_constructor():
    with patch.object(Backtesting, '__init__') as native:
        with pytest.raises(EquityAdmissionError): EquityBacktesting({})
        native.assert_not_called()


def test_native_matcher_order_wallet_and_pair_generator_not_replaced():
    assert all(name not in EquityFixtureLifecycleMixin.__dict__ for name in (
        '__init__', 'time_pair_generator', '_time_pair_generator_det', 'backtest_loop',
        '_enter_trade', '_try_close_open_order', '_process_exit_order'))
