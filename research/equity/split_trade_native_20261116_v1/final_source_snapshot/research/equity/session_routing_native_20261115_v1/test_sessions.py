"""Native generator/callback/wallet checks; synthetic inputs are not returns."""
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from bootstrap import boot

boot()
sys.path.append(str(Path(__file__).resolve().parent.parent / 'cash_dividends_native_20261114_v1'))
from test_dividends import account, contract, fill, zero_trade
from freqtrade.data.equity_provider import EquityDataProvider, EquitySchedule
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import CandleType, TradingMode
from freqtrade.optimize.backtesting import Backtesting, HEADERS
from freqtrade.optimize.equity_sessions import EquityDailySessionPlan, EquitySessionRoutingMixin
from freqtrade.optimize.equity_fee_execution import EquityFeeFillMixin
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.resolvers import StrategyResolver


class RoutedProbe(EquitySessionRoutingMixin, EquityFeeFillMixin, Backtesting):
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


def routed(market='XNYS'):
    c = contract(market)
    a = account(c, events=())
    a.__class__ = RoutedProbe
    pair = 'ABC/' + c.currency
    # Exactly one preceding completed session, outside the funded clock.
    prior = c.sessions[0].opens - timedelta(days=1)
    labels = [pd.Timestamp(prior.date(), tz='UTC')] + [pd.Timestamp(s.label, tz='UTC') for s in c.sessions]
    opens = [prior] + [s.opens for s in c.sessions]
    closes = [prior + timedelta(hours=6)] + [s.closes for s in c.sessions]
    schedule = pd.DataFrame({'open': opens, 'close': closes, 'break_start': pd.NaT, 'break_end': pd.NaT}, index=labels)
    schedule['break_start'] = [pd.NaT] + [s.break_start for s in c.sessions]
    schedule['break_end'] = [pd.NaT] + [s.break_end for s in c.sessions]
    schedule.index = schedule.index.as_unit('ns')
    for column in ['open', 'close', 'break_start', 'break_end']:
        schedule[column] = pd.to_datetime(schedule[column], utc=True).astype('datetime64[ns, UTC]')
    source = pd.DataFrame({'date': labels, 'open': 100., 'high': 102., 'low': 98., 'close': [101. if i % 2 == 0 else 99. for i in range(len(labels))], 'volume': 1000.})
    sc = EquitySessionContract(pair, market, tuple(t.strftime('%Y-%m-%d') for t in labels))
    schedules = {pair: EquitySchedule(sc, schedule)}
    a.equity_session_plan = EquityDailySessionPlan(schedules, first_label=labels[1], last_label=labels[-1])
    cfg = dict(a.config, timeframe='1d', candle_type_def=CandleType.SPOT, strategy='ClockProbe',
               strategy_path=str(Path(__file__).resolve().parent), user_data_dir=Path(__file__).resolve().parents[3] / 'user_data',
               stake_amount=100., trading_mode='spot')
    a.config = cfg
    a.dataprovider = EquityDataProvider(cfg, schedules)
    a.strategy = StrategyResolver.load_strategy(cfg)
    a.strategy.dp = a.dataprovider
    a.dataprovider._equity_raw[pair] = source.copy(deep=True)
    a.timeframe = '1d'
    a.timeframe_td = timedelta(days=1)
    a.timeframe_detail_td = None
    a.dynamic_pairlist = False
    a._can_short = False
    a.required_startup = 0
    a.trading_mode = TradingMode.SPOT
    a._set_progress_step = lambda *args: None
    a._increment_progress = lambda: None
    a.check_abort = lambda: None
    a._is_backtest_runmode = False
    return a, pair, source


@pytest.mark.parametrize('market', ['XNYS', 'XHKG'])
def test_native_generator_has_exact_clocks_and_prior_close_signals(market):
    a, pair, source = routed(market)
    before = source.copy(deep=True)
    rows = a._get_ohlcv_as_lists({pair: source})
    pd.testing.assert_frame_equal(source, before)
    plan = a.equity_session_plan
    observed = []

    def callback(current_time, **kwargs):
        # Observe real wallet/provider BEFORE the inherited generator yields.
        assert a.wallets.asof == current_time
        visible, known = a.dataprovider.get_analyzed_dataframe(pair, '1d')
        assert known < current_time
        observed.append((current_time, visible.iloc[-1].enter_tag))

    a.strategy.bot_loop_start = callback
    result = list(a.time_pair_generator(plan.opens[0], plan.opens[-1], [pair], rows))
    assert [r[0] for r in result] == list(plan.opens)
    assert [r[2][HEADERS.index('enter_tag')] for r in result] == source.date.iloc[:-1].dt.strftime('%Y-%m-%d').tolist()
    expected = [int(i % 2 == 0) for i in range(len(plan.opens))]
    assert [r[2][HEADERS.index('enter_long')] for r in result] == expected
    assert [r[4] for r in result] == ['long' if value else None for value in expected]
    assert result[0][3] is False and result[-1][3] is True
    assert observed == list(zip(plan.opens, source.date.iloc[:-1].dt.strftime('%Y-%m-%d')))
    assert not Trade.get_trades_proxy()


def test_future_prices_flags_and_tags_do_not_change_prior_execution():
    a, pair, source = routed()
    first = a._get_ohlcv_as_lists({pair: source})[pair]
    changed = source.copy()
    changed.loc[2:, ['open', 'high', 'low', 'close']] *= 10
    changed.loc[2:, 'close'] = changed.loc[2:, 'open']
    second = a._get_ohlcv_as_lists({pair: changed})[pair]
    assert first[0] == second[0]
    assert first[1][5:] == second[1][5:]


def test_settlement_wallet_is_advanced_before_next_native_callback():
    a, pair, source = routed()
    rows = a._get_ohlcv_as_lists({pair: source})
    plan = a.equity_session_plan
    generator = a.time_pair_generator(plan.opens[0], plan.opens[-1], [pair], rows)
    next(generator)
    t = zero_trade(pair, plan.opens[0])
    fill(a, t, plan.opens[0], 2, 100)
    fill(a, t, plan.opens[0], 2, 100, False)
    assert a.wallets.get_free('USD') < a.wallets.get_total('USD')
    list(generator)
    assert a.wallets.asof == plan.opens[-1]
    assert a.wallets.get_free('USD') == a.wallets.get_total('USD')


@pytest.mark.parametrize('fault', ['detail', 'dynamic', 'short', 'mode', 'dry', 'currency', 'database'])
def test_reject_unsupported_native_paths(fault):
    a, pair, source = routed()
    if fault == 'detail': a.timeframe_detail_td = timedelta(minutes=5)
    if fault == 'dynamic': a.dynamic_pairlist = True
    if fault == 'short': a._can_short = True
    if fault == 'mode': a.trading_mode = TradingMode.FUTURES
    if fault == 'dry': a.config['dry_run'] = False
    if fault == 'currency': a.config['stake_currency'] = 'HKD'
    if fault == 'database': Trade.use_db = True
    with pytest.raises(EquityAdmissionError):
        a._get_ohlcv_as_lists({pair: source})


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'offgrid', 'short_flag', 'nonbinary', 'schedule', 'universe'])
def test_reject_incomplete_or_changed_inputs(fault):
    a, pair, source = routed()
    if fault == 'missing': source = source.iloc[:-1]
    if fault == 'duplicate': source = pd.concat([source, source.iloc[-1:]])
    if fault == 'offgrid': source.loc[1, 'date'] += timedelta(days=1)
    if fault == 'short_flag': source['enter_short'] = 1
    if fault == 'nonbinary': source['exit_short'] = .5
    if fault == 'schedule': a.dataprovider.schedules[pair].frame.iloc[0, 0] += timedelta(minutes=1)
    if fault == 'universe': pair = 'OTHER/USD'
    with pytest.raises(EquityAdmissionError):
        a._get_ohlcv_as_lists({pair: source})


@pytest.mark.parametrize('fault', ['start', 'end', 'wallet_clock', 'wallet_type', 'provider'])
def test_reject_bad_generator_before_callback(fault):
    a, pair, source = routed()
    p = a.equity_session_plan
    start, end = p.opens[0], p.opens[-1]
    if fault == 'start': start -= timedelta(days=1)
    if fault == 'end': end += timedelta(days=1)
    if fault == 'wallet_clock': a.wallets.advance(p.opens[1])
    if fault == 'wallet_type': a.wallets = object()
    if fault == 'provider': a.dataprovider = object()
    with pytest.raises(EquityAdmissionError):
        next(a._time_generator(start, end))


def test_bound_schedule_cannot_change_mid_iteration():
    a, pair, source = routed()
    p = a.equity_session_plan
    generator = a._time_generator(p.opens[0], p.opens[-1])
    next(generator)
    a.dataprovider.schedules[pair].frame.iloc[0, 0] += timedelta(minutes=1)
    with pytest.raises(EquityAdmissionError): next(generator)


def test_existing_native_fills_cannot_be_inherited_by_new_window():
    a, pair, source = routed()
    p = a.equity_session_plan
    t = zero_trade(pair, p.opens[0])
    fill(a, t, p.opens[0], 1, 100)
    with pytest.raises(EquityAdmissionError):
        next(a._time_generator(p.opens[0], p.opens[-1]))


def test_lunch_schedule_and_cash_contract_must_agree():
    a, pair, source = routed('XHKG')
    schedule = a.dataprovider.schedules[pair]
    schedule.frame['break_start'] = pd.NaT
    schedule.frame['break_end'] = pd.NaT
    plan = a.equity_session_plan
    a.equity_session_plan = EquityDailySessionPlan({pair: schedule}, first_label=plan.labels[0], last_label=plan.labels[-1])
    with pytest.raises(EquityAdmissionError):
        next(a._time_generator(plan.opens[0], plan.opens[-1]))
