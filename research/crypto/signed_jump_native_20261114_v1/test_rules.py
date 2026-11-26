"""Source formula, minute-volume and causal input checks; no account claims."""
import numpy as np
import pandas as pd
import pytest
from bootstrap import boot

boot()
from freqtrade.data.signed_jump import daily_signed_variation

def observations(steps=None):
    dates=pd.date_range('2024-12-31 23:55',periods=865,freq='5min',tz='UTC')
    steps=np.tile([.001,-.002],432)if steps is None else np.full(864,steps)
    price=100*np.exp(np.r_[0,np.cumsum(steps)]);base=np.full(len(price),5.)
    quote=price*base
    return pd.DataFrame({'date':dates,'available_at':dates+pd.Timedelta(minutes=5),
                         'vwap':quote/base,'base_volume':base,'quote_volume':quote})

@pytest.mark.parametrize('value,rsj,rsk',[('positive',1,1),('negative',-1,-1),('mixed',-.6,None)])
def test_known_source_moments(value,rsj,rsk):
    frame=observations({'positive':.001,'negative':-.001,'mixed':None}[value])
    row=daily_signed_variation(frame).loc['2025-01-01']
    assert row.rsj==pytest.approx(rsj,abs=1e-11)
    assert row.observed_returns==288
    assert row.sj==pytest.approx(row.up-row.down)
    assert row.rv==pytest.approx(row.up+row.down)
    if rsk is not None:
        assert row.rsk==pytest.approx(rsk,abs=1e-11)
        assert row.rkt==pytest.approx(1,abs=1e-11)

@pytest.mark.parametrize('fault',['missing','duplicate','outoforder','naive','minute','future','nan_volume','negative_volume','zero_filled','unknown_nonzero','vwap_mismatch','nonpositive','zero_quote_nonzero_base'])
def test_invalid_observation_contract_rejects(fault):
    f=observations()
    if fault=='missing':f=f.drop(300)
    elif fault=='duplicate':f.loc[300,'date']=f.loc[299,'date']
    elif fault=='outoforder':f=f.iloc[::-1]
    elif fault=='naive':f['date']=f.date.dt.tz_localize(None)
    elif fault=='minute':f['date']+=pd.Timedelta(minutes=1)
    elif fault=='future':f.loc[300,'available_at']+=pd.Timedelta(minutes=1)
    elif fault=='nan_volume':f.loc[300,'base_volume']=np.nan
    elif fault=='negative_volume':f.loc[300,'quote_volume']=-1
    elif fault=='zero_filled':f.loc[300,['base_volume','quote_volume']]=0
    elif fault=='unknown_nonzero':f.loc[300,'vwap']=np.nan
    elif fault=='vwap_mismatch':f.loc[300,'vwap']*=2
    elif fault=='nonpositive':f.loc[300,'vwap']=0
    else:f.loc[300,'quote_volume']=0
    with pytest.raises(ValueError):daily_signed_variation(f)

def test_zero_volume_is_unknown_and_invalidates_both_adjacent_day_returns():
    f=observations();f.loc[288,['base_volume','quote_volume']]=0;f.loc[288,'vwap']=np.nan
    result=daily_signed_variation(f)
    assert result.loc['2025-01-01',['rsj','rsk','rkt']].isna().all()
    assert result.loc['2025-01-02',['rsj','rsk','rkt']].isna().all()
    assert result.loc['2025-01-03',['rsj','rsk','rkt']].notna().all()

def test_flat_price_retains_known_rv_zero_and_unknown_normalized_moments():
    f=observations(0.);r=daily_signed_variation(f).loc['2025-01-01']
    assert r.rv==0 and r.day_return==0 and r.observed_returns==288
    assert r[['rsj','rsk','rkt']].isna().all()

def test_price_units_do_not_change_normalized_moments_and_prefix_is_causal():
    f=observations();base=daily_signed_variation(f);changed=f.copy()
    changed['quote_volume']*=10;changed['vwap']=changed.quote_volume/changed.base_volume
    other=daily_signed_variation(changed)
    np.testing.assert_allclose(base[['rsj','rsk','rkt']],other[['rsj','rsk','rkt']],atol=1e-9,rtol=1e-9,equal_nan=True)
    cutoff=pd.Timestamp('2025-01-03',tz='UTC');past=f.date<cutoff
    changed=f.copy();changed.loc[~past,'quote_volume']*=3.14;changed['vwap']=changed.quote_volume/changed.base_volume
    altered=daily_signed_variation(changed);short=daily_signed_variation(f.loc[past])
    pd.testing.assert_frame_equal(base.loc[base.index<cutoff],altered.loc[altered.index<cutoff],check_exact=True)
    pd.testing.assert_frame_equal(base.loc[base.index<cutoff],short,check_exact=True)

@pytest.mark.parametrize('mode,diagnostic',[('live',True),('dry_run',True),('backtest',False)])
def test_input_only_strategy_rejects_services_or_financial_use(mode,diagnostic):
    from SignedJumpInputs import SignedJumpInputs
    from freqtrade.enums import RunMode
    owner=object.__new__(SignedJumpInputs);owner.config={'runmode':RunMode(mode),'research_diagnostic_only':diagnostic}
    with pytest.raises(ValueError):owner.bot_start()

def test_input_only_loop_cannot_be_an_all_cash_backtest():
    from SignedJumpInputs import SignedJumpInputs
    owner=object.__new__(SignedJumpInputs)
    assert owner.confirm_trade_entry()is False
    with pytest.raises(ValueError):owner.bot_loop_start()


def test_native_loop_wrapper_suppresses_error_so_lifecycle_guard_is_required():
    from SignedJumpInputs import SignedJumpInputs
    from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
    owner=object.__new__(SignedJumpInputs)
    assert strategy_safe_wrapper(owner.bot_loop_start,supress_error=True)() is None


def test_actual_native_set_strategy_rejects_before_matching_loop():
    from types import SimpleNamespace
    from SignedJumpInputs import SignedJumpInputs
    from freqtrade.enums import TradingMode
    from freqtrade.optimize.backtesting import Backtesting
    owner=object.__new__(SignedJumpInputs)
    owner.order_types={'stoploss_on_exchange':True}
    shell=SimpleNamespace(dataprovider=None,wallets=None,trading_mode=TradingMode.FUTURES)
    with pytest.raises(ValueError,match='cannot start a native financial backtest'):
        Backtesting._set_strategy(shell,owner)
