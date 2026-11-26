"""Native-wallet and native-precision preparation checks, never strategy profits."""
import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT.parent / 'CZ-Agent/vendor/freqtrade'))
spec = importlib.util.spec_from_file_location('copula_execution_test_owner', ROOT / 'freqtrade/data/copula_execution.py')
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
from freqtrade.enums import RunMode, TradingMode
from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.wallets import Wallets


class PrecisionExchange(Exchange):
    def close(self):
        pass


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def wallet(capital=1000):
    exchange = object.__new__(PrecisionExchange)
    exchange._api = SimpleNamespace(precisionMode=TICK_SIZE)
    exchange.get_proxy_coin = lambda: 'USDT'
    exchange.get_pair_base_currency = lambda pair: pair.split('/')[0]
    exchange.get_precision_amount = lambda pair: .1
    exchange.get_contract_size = lambda pair: 1.
    exchange.get_min_pair_stake_amount = lambda pair, rate, stoploss, leverage: 5.25 / leverage
    exchange.get_max_pair_stake_amount = lambda pair, rate, leverage: 100000. / leverage
    config = dict(runmode=RunMode.BACKTEST, dry_run=True, dry_run_wallet=float(capital), trading_mode=TradingMode.FUTURES,
                  stake_currency='USDT', tradable_balance_ratio=.99, stake_amount='unlimited')
    return Wallets(config, exchange, is_backtest=True), exchange


def forecasts(states=(0, 1, 1, 0, -1, 0), weeks=None):
    index = pd.date_range('2025-01-03', periods=len(states), freq='h', tz='UTC')
    return pd.DataFrame(dict(week=weeks or ['w1'] * len(states), model_status='FITTED',
        state_10=states, state_15=states, state_20=states,
        symbol1='EOS', symbol2='XRP', beta1=10., beta2=20.), index=index)


@pytest.mark.parametrize('capital', [1000, 500])
def test_actual_native_wallet_budget_applies_ratio_once(capital):
    w, e = wallet(capital)
    legs = (('EOS/USDT:USDT', 10.), ('XRP/USDT:USDT', 20.))
    prices = dict(zip((p for p, _ in legs), [2., 1.]))
    frozen = owner.freeze_weekly_units(w, e, legs, prices)
    assert frozen['native_gross_budget'] == capital * .99
    # Native flooring happens independently; exact beta ratios can have one
    # tick of tracking error and must not be imposed by up-rounding a leg.
    denominator = sum(Decimal(str(beta)) * Decimal(str(prices[pair])) for pair, beta in legs)
    for pair, beta in legs:
        expected = (Decimal(str(capital)) * Decimal('.99') * Decimal(str(beta)) / denominator).quantize(Decimal('.1'), rounding=ROUND_DOWN)
        assert frozen['quantities'][pair] == float(expected)
    assert frozen['gross_at_week_open'] <= capital * .99
    before = repr(w)
    admitted = owner.joint_entry_admission(w, e, frozen, prices, .0015)
    assert admitted['status'] == 'ADMIT_BOTH' and admitted['native_fills'] == 0
    assert before == repr(w) and not Trade.get_trades_proxy()


def test_either_leg_below_minimum_rejects_both_without_upscaling():
    w, e = wallet()
    frozen = {'quantities': {'EOS/USDT:USDT': .1, 'XRP/USDT:USDT': 10.}}
    actual = owner.joint_entry_admission(w, e, frozen, dict(zip(frozen['quantities'], [1., 1.])), .0015)
    assert actual['status'] == 'REJECT_BOTH'
    assert actual['quantities'] == frozen['quantities']
    assert actual['reasons'][0]['pair'] == 'EOS/USDT:USDT'


def test_joint_margin_and_cost_cannot_use_two_independent_wallet_budgets():
    w, e = wallet()
    frozen = {'quantities': {'EOS/USDT:USDT': 2000., 'XRP/USDT:USDT': 2000.}}
    result = owner.joint_entry_admission(w, e, frozen, dict(zip(frozen['quantities'], [1., 1.])), .0015)
    assert result['status'] == 'REJECT_BOTH'
    assert any(r['reason'] == 'native_joint_margin_and_cost' for r in result['reasons'])


def test_absent_native_minimum_is_not_a_zero_cost_order_contract():
    w, e = wallet()
    e.get_min_pair_stake_amount = lambda *args, **kwargs: None
    frozen = {'quantities': {'EOS/USDT:USDT': 10., 'XRP/USDT:USDT': 10.}}
    result = owner.joint_entry_admission(w, e, frozen, dict(zip(frozen['quantities'], [1., 1.])), .0015)
    assert result['status'] == 'REJECT_BOTH'
    assert len([r for r in result['reasons'] if r['reason'] == 'native_minimum_unknown']) == 2


@pytest.mark.parametrize('fault', ['paper', 'live', 'spot'])
def test_paper_live_and_spot_wallets_do_not_enter_the_research_sizer(fault):
    w, e = wallet()
    if fault == 'paper': w._is_backtest = False
    if fault == 'live': w._config['dry_run'] = False
    if fault == 'spot': w._config['trading_mode'] = TradingMode.SPOT
    with pytest.raises(ValueError):
        owner.freeze_weekly_units(w, e, (('EOS/USDT:USDT', 1.), ('XRP/USDT:USDT', 1.)), {'EOS/USDT:USDT': 1., 'XRP/USDT:USDT': 1.})


@pytest.mark.parametrize('quantity,rate', [(.1, .7321), (123.4, .8317), (495., 1.003), (180.3, 2.7)])
def test_native_stake_roundtrip_retains_weekly_units(quantity, rate):
    _, exchange = wallet()
    stake = owner.native_stake_for_quantity(exchange, 'EOS/USDT:USDT', quantity, rate)
    assert exchange.amount_to_precision('EOS/USDT:USDT', stake / rate * 3) == quantity


def test_weekly_roll_has_old_exit_then_flat_budget_then_new_entry():
    f = forecasts((1, 1, 1), ['old', 'new', 'new'])
    result = owner.intent_schedule(f, 10)
    boundary = f.index[1]
    roll = [r for r in result if r['decision_time'] == boundary]
    assert [(r['action'], r['at'] - boundary) for r in roll] == [
        ('exit', pd.Timedelta(minutes=5)), ('budget', pd.Timedelta(minutes=10)), ('entry', pd.Timedelta(minutes=10))]


def test_initial_state_starts_flat_and_ordinary_decisions_have_one_native_lag():
    f = forecasts()
    result = owner.intent_schedule(f, 10)
    assert result[0]['action'] == 'budget'
    for row in result:
        assert row['at'] == row['decision_time'] + pd.Timedelta(minutes=5)
    assert [r['state'] for r in result if r['action'] == 'entry'] == [1, -1]


def test_truncated_and_future_changed_schedules_preserve_past_intents():
    f = forecasts()
    cutoff = f.index[3]
    full = [r for r in owner.intent_schedule(f, 10) if r['decision_time'] < cutoff]
    assert full == owner.intent_schedule(f.loc[f.index < cutoff], 10)
    changed = f.copy()
    changed.loc[changed.index >= cutoff, ['state_10']] = 0
    assert full == [r for r in owner.intent_schedule(changed, 10) if r['decision_time'] < cutoff]


@pytest.mark.parametrize('fault', ['missing_hour', 'duplicate', 'naive', 'direct_flip', 'beta_change', 'nonfitted_active', 'unknown_cash_week'])
def test_invalid_source_contract_rejects(fault):
    f = forecasts()
    if fault == 'missing_hour': f = f.drop(f.index[2])
    if fault == 'duplicate': f.index = [f.index[0]] * len(f)
    if fault == 'naive': f.index = f.index.tz_localize(None)
    if fault == 'direct_flip': f.loc[f.index[2], 'state_10'] = -1
    if fault == 'beta_change': f.loc[f.index[2], 'beta1'] = 15
    if fault == 'nonfitted_active': f.loc[f.index[1], 'model_status'] = 'INSUFFICIENT_STATIONARITY'
    if fault == 'unknown_cash_week': f.loc[f.index[0], 'model_status'] = 'INPUT_FAILURE'
    with pytest.raises(ValueError): owner.intent_schedule(f, 10)


def test_open_native_trade_refuses_weekly_rebudget():
    w, e = wallet()
    t = LocalTrade(id=1, pair='EOS/USDT:USDT', amount=1., is_open=True, open_date=datetime(2025,1,1,tzinfo=UTC))
    LocalTrade.add_bt_trade(t)
    with pytest.raises(ValueError):
        owner.freeze_weekly_units(w, e, (('EOS/USDT:USDT', 1.), ('XRP/USDT:USDT', 1.)), {'EOS/USDT:USDT': 1., 'XRP/USDT:USDT': 1.})
