"""Capital ceilings, native informative timing and fixed-tick execution guards."""
import importlib.util
import sys
from datetime import UTC,datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path[:0]=[str(ROOT.parent/'CZ-Agent/vendor/freqtrade'),str(HERE),str(HERE/'sources')]
from SourcePortfolio import SourceReversal,ReversalInADXTrend
from freqtrade.enums import RunMode,TradingMode
from freqtrade.persistence import Trade

spec=importlib.util.spec_from_file_location('fixed_tick',ROOT/'freqtrade/optimize/frozen_price_precision.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Mixin=module.FrozenPricePrecisionMixin


def source():
    owner=object.__new__(SourceReversal)
    owner._fatal_error=None;owner.research_trace={'budgets':[],'confirmed':[]};owner._latest_budget={}
    return owner


def test_minimum_does_not_upsize_native_slot_or_confirm_an_entry():
    a=source();at=datetime(2025,1,1,tzinfo=UTC)
    assert a.custom_stake_amount('BTC/USDT:USDT',at,100,60,100,500,1,None,'long')==0
    assert not a.confirm_trade_entry('BTC/USDT:USDT','limit',1,100,'GTC',at,None,'long')


def test_actual_native_budget_caps_quantity_and_uses_no_curve_scaling():
    a=source();at=datetime(2025,1,1,tzinfo=UTC)
    assert a.custom_stake_amount('BTC/USDT:USDT',at,100,123.75,100,120,1,None,'long')==120
    assert a.confirm_trade_entry('BTC/USDT:USDT','limit',1.2,100,'GTC',at,None,'long')
    assert not a.confirm_trade_entry('BTC/USDT:USDT','limit',1.21,100,'GTC',at,None,'long')


@pytest.mark.parametrize('fault',['unknown_min','leverage','short','nan'])
def test_bad_callback_inputs_persist_fatal_and_return_zero(fault):
    a=source();v=dict(pair='BTC/USDT:USDT',current_time=datetime(2025,1,1,tzinfo=UTC),
        current_rate=100,proposed_stake=120,min_stake=10,max_stake=500,leverage=1,entry_tag=None,side='long')
    if fault=='unknown_min':v['min_stake']=None
    if fault=='leverage':v['leverage']=3
    if fault=='short':v['side']='short'
    if fault=='nan':v['proposed_stake']=float('nan')
    assert a.custom_stake_amount(**v)==0 and a._fatal_error


def test_regime_entry_requires_both_sources_and_completed_clock():
    a=object.__new__(ReversalInADXTrend)
    frame=pd.DataFrame({'date':pd.to_datetime(['2025-01-01T10:45Z','2025-01-01T10:50Z','2025-01-01T10:55Z']),
        'date_1h':pd.to_datetime(['2025-01-01T09:00Z','2025-01-01T09:00Z','2025-01-01T10:00Z']),
        'rsi':[20.,40.,20.],'slowk':10.,'bb_lowerband':101.,'close':100.,'CDLHAMMER':100,
        'adx_source_long_1h':[0,1,1]})
    result=a.populate_entry_trend(frame.copy(),{})
    assert result.enter_long.tolist()==[0,0,1]
    frame.loc[0,'date_1h']=pd.Timestamp('2025-01-01T10:00Z')
    with pytest.raises(ValueError,match='not complete'):a.populate_entry_trend(frame,{})


def tick_shell():
    a=Mixin();a.config={'runmode':RunMode.BACKTEST,'dry_run':True,
        'exchange':{'pair_whitelist':['BTC/USDT:USDT']}}
    a.trading_mode=TradingMode.FUTURES
    a.exchange=SimpleNamespace(precision_mode_price=4,get_precision_price=lambda pair:.1)
    a.price_pair_prec={'BTC/USDT:USDT':pd.Series([1e-9],index=pd.to_datetime(['2025-01-01'],utc=True))}
    return a


def test_future_monthly_inferred_precision_is_never_used():
    a=tick_shell()
    with patch.object(Trade,'use_db',False):
        a.setup_frozen_price_precision(pairs=('BTC/USDT:USDT',))
        assert a.get_pair_precision('BTC/USDT:USDT',datetime(2025,1,1,tzinfo=UTC))==(.1,4)
        a.price_pair_prec['BTC/USDT:USDT'].iloc[0]=10000
        assert a.get_pair_precision('BTC/USDT:USDT',datetime(2025,1,1,tzinfo=UTC))==(.1,4)


@pytest.mark.parametrize('fault',['unset','mode','dry','db','tick','universe','clock','pair'])
def test_fixed_precision_rejects_context_drift(fault):
    a=tick_shell();at=datetime(2025,1,1,tzinfo=UTC)
    with patch.object(Trade,'use_db',False):
        if fault!='unset':a.setup_frozen_price_precision(pairs=('BTC/USDT:USDT',))
        if fault=='mode':a.config['runmode']=RunMode.LIVE
        if fault=='dry':a.config['dry_run']=False
        if fault=='db':Trade.use_db=True
        if fault=='tick':a.exchange.get_precision_price=lambda pair:.01
        if fault=='universe':a.config['exchange']['pair_whitelist']=['ETH/USDT:USDT']
        if fault=='clock':at=at.replace(tzinfo=None)
        with pytest.raises(ValueError):a.get_pair_precision('ETH/USDT:USDT'if fault=='pair'else'BTC/USDT:USDT',at)
