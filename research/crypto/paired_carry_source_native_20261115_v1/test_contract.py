"""Native common-lot sizing, fee budgets, entry/clock and mode boundaries."""
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path[:0]=[str(ROOT.parent/'CZ-Agent/vendor/freqtrade'),str(HERE)]
for name,path in [('freqtrade.data.paired_carry',ROOT/'freqtrade/data/paired_carry.py'),
                  ('carry_precision',ROOT/'freqtrade/optimize/carry_precision.py')]:
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
from freqtrade.data.paired_carry import common_quantity
from carry_precision import CarryPrecisionMixin
from CarryLeg import CarrySpot, CarryShort
from freqtrade.enums import RunMode, TradingMode
from freqtrade.exchange.exchange_utils import amount_to_precision
from freqtrade.persistence import Trade


def test_common_quantity_floors_both_grids_and_includes_both_leg_costs():
    q,step=common_quantity(125,93576,93548.9,.00001,.001,.002,.0015)
    assert str(q)=='.001'.replace('.','0.',1) and str(step)=='0.001'
    assert float(q)*93576*1.002<=125*.95
    q,step=common_quantity(100,10,11,.02,.03,.001,.002)
    assert str(step)=='0.06' and q%step==0
    assert float(q)*11*1.002<=95


@pytest.mark.parametrize('fault',['nan','zero','negative','boolean','fee','too_small'])
def test_bad_or_infeasible_quantities_do_not_upsize(fault):
    args=[125,93576,93548.9,.00001,.001,.002,.0015]
    if fault=='nan':args[1]=float('nan')
    if fault=='zero':args[1]=0
    if fault=='negative':args[4]=-.001
    if fault=='boolean':args[0]=True
    if fault=='fee':args[5]=1
    if fault=='too_small':args[0]=1
    with pytest.raises(ValueError):common_quantity(*args)


@pytest.mark.parametrize('cls,side',[(CarrySpot,'long'),(CarryShort,'short')])
def test_actual_callbacks_request_exact_native_quantity_and_reject_minimum_or_wrong_clock(cls,side):
    config={'runmode':RunMode.BACKTEST,'dry_run':True,'research_carry_leg':True,
        'max_open_trades':1,'research_quantity':.002}
    owner=cls(config);owner.bot_start();at=datetime(2025,1,1,0,5,tzinfo=UTC)
    stake=owner.custom_stake_amount('X',at,93548.9,247.5,75,247.5,1,None,side)
    quantity=amount_to_precision(stake/93548.9,.001,4)
    assert quantity==.002
    assert owner.confirm_trade_entry('X','market',quantity,93548.9,'GTC',at,None,side)
    assert not owner.confirm_trade_entry('X','market',.001,93548.9,'GTC',at,None,side)
    assert owner._fatal_error
    owner.bot_start()
    assert owner.custom_stake_amount('X',at,93548.9,247.5,200,247.5,1,None,side)==0
    assert owner._fatal_error


def test_signals_are_static_one_entry_and_independent_of_future_quote_values():
    dates=pd.date_range('2025-01-01',periods=50,freq='5min',tz='UTC')
    data=pd.DataFrame({'date':dates,'close':100.})
    for cls,side in [(CarrySpot,'enter_long'),(CarryShort,'enter_short')]:
        owner=cls({});original=owner.populate_entry_trend(data.copy(),{})
        changed=data.copy();changed.loc[1:,'close']=1e5
        other=owner.populate_entry_trend(changed,{})
        assert original[side].sum()==1 and original[side].tolist()==other[side].tolist()


@pytest.mark.parametrize('mode',[TradingMode.SPOT,TradingMode.FUTURES])
def test_fixed_precision_ignores_future_monthly_inference_and_enforces_mode(mode):
    a=CarryPrecisionMixin();a.config={'runmode':RunMode.BACKTEST,'dry_run':True,
        'research_carry_leg':True,'exchange':{'pair_whitelist':['X']}}
    a.trading_mode=mode;a.exchange=SimpleNamespace(precision_mode_price=4,get_precision_price=lambda p:.01)
    a.price_pair_prec={'X':1e-12}
    with patch.object(Trade,'use_db',False):
        a.setup_frozen_price_precision(pairs=('X',))
        assert a.get_pair_precision('X',datetime(2025,1,1,tzinfo=UTC))==(.01,4)
        a.price_pair_prec={'X':1000}
        assert a.get_pair_precision('X',datetime(2025,1,1,tzinfo=UTC))==(.01,4)
        a.trading_mode=TradingMode.FUTURES if mode==TradingMode.SPOT else TradingMode.SPOT
        with pytest.raises(ValueError):a.get_pair_precision('X',datetime(2025,1,1,tzinfo=UTC))


def test_every_registered_intent_roundtrips_through_native_amount_precision():
    plan=json.loads((HERE/'plan.json').read_text())
    for c in plan['candidates']:
        market=json.loads(Path(c['market_snapshot']['path']).read_text())['markets'][c['pairs'][0]]
        q=c['quantity'];step=market['precision']['amount']
        for rate in [93548.9,3336.57,100.,99.999]:
            assert amount_to_precision((q*rate)/rate,step,4)==pytest.approx(q,abs=1e-12)


def test_native_amount_reserve_can_reject_quantity_that_passes_raw_market_minimum():
    from review_native_limits import native_minimum
    plan=json.loads((HERE/'plan.json').read_text())
    c=next(c for c in plan['candidates']if c['name']=='carry_BTC_futures_500_conservative')
    cfg=json.loads(Path(c['config']['path']).read_text())
    markets=json.loads(Path(c['market_snapshot']['path']).read_text())['markets']
    pair=c['pairs'][0];rate=93648.3
    minimum=native_minimum(cfg,markets,pair,rate)
    assert markets[pair]['limits']['cost']['min']<c['quantity']*rate<minimum
    assert minimum==pytest.approx(.001*rate*1.05)
    owner=CarryShort({**cfg,'runmode':RunMode.BACKTEST});owner.bot_start()
    assert owner.custom_stake_amount(pair,datetime(2025,1,1,0,5,tzinfo=UTC),rate,
        123.75,minimum,123.75,1,None,'short')==0
    assert owner._fatal_error
