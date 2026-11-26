"""Source-state, causal fitted model, input and native-adapter checks."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,'/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sys.path.insert(0,str(HERE.parent/'tsmom_risk_native_20261114_v1'));sys.path.insert(0,str(HERE))
from PairWeights import SYMBOLS,fit_pairs,pair_weights,states_from_z,validate_prices


@pytest.fixture(scope='module')
def close():return pd.read_parquet(ROOT/'user_data/crypto_quant/pairs_cointegration_native_20261114_v1/spot_daily.parquet')


def test_exact_source_strict_state_thresholds():
    spec=importlib.util.spec_from_file_location('source_math_test',HERE/'source/pairs/backtest.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    z=pd.Series([np.nan,2,2.1,-5,-3,-.5,-.49,5,5,0,-5,0,4,-4,.5,-.5])
    actual=states_from_z(z)
    np.testing.assert_array_equal(actual,m.generate_signal(z).to_numpy())
    assert actual[7]==-1 and actual[8]==0  # Entry beyond4 from flat, stop on next observation.


@pytest.mark.parametrize('fault',['gap','unknown','negative','reordered'])
def test_no_silent_input_imputation(close,fault):
    x=close.copy()
    if fault=='gap':x=x.drop(x.index[20])
    elif fault=='unknown':x.iloc[30,0]=np.nan
    elif fault=='negative':x.iloc[40,0]=-1
    else:x=x[SYMBOLS[::-1]]
    with pytest.raises(ValueError):validate_prices(x)


@pytest.mark.parametrize('mode',['naive','adjusted'])
def test_no_training_or_evaluation_future_dependence(close,mode):
    cutoff=pd.Timestamp('2025-06-01',tz='UTC')
    days=pd.date_range(pd.Timestamp('2025-01-01',tz='UTC'),cutoff,freq='D')
    weights,diagnostics,states=pair_weights(close,days,mode)
    future=close.copy();future.loc[future.index>=cutoff]*=4
    changed,other,_=pair_weights(future,days,mode)
    pd.testing.assert_frame_equal(weights,changed,check_exact=True)
    assert diagnostics['selected_pairs']==other['selected_pairs']
    assert weights.abs().sum(axis=1).le(1+1e-12).all()
    prefix,_,_=pair_weights(close.loc[:cutoff-pd.Timedelta(days=1)],days,mode)
    pd.testing.assert_frame_equal(weights,prefix,check_exact=True)
    assert set(states.state.unique())<=set([-1,0,1])


def test_actual_native_prefix_receipts():
    j=json.loads((HERE/'preflight.json').read_text())
    assert j['passed'] and j['source_numeric_max_error']<1e-9
    assert len(j['actual_native_strategies'])==3
    assert sum(len(x['prefix_refits']) for x in j['actual_native_strategies'])==8


def test_original_protocol_and_source_pool_unchanged():
    import hashlib
    p=json.loads((HERE/'protocol.json').read_text());f=json.loads((HERE/'protocol_freeze.json').read_text())
    assert hashlib.sha256((HERE/'protocol.json').read_bytes()).hexdigest()==f['protocol']['sha256']
    assert p['symbols']==SYMBOLS and p['account_start']=='2025-01-01T00:00:00Z'
    assert p['total_cost_bps_per_side']==[15,8] and p['unique_native_accounts']==12
