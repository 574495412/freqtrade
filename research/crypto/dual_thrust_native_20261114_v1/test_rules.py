"""Boundary and state-machine checks independent of financial outcomes."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from DualRules import completed_states


def data(days=45):
    return pd.DataFrame(dict(date=pd.date_range('2024-12-01',periods=days*24,freq='h',tz='UTC'),
                             open=100.,high=110.,low=90.,close=100.,volume=1.))


def test_exact_upper_renews_strict_lower_cancels_and_band_retains():
    f=data();f.loc[f.date.eq(pd.Timestamp('2025-01-01T01:00Z')),'close']=105.
    f.loc[f.date.eq(pd.Timestamp('2025-01-01T05:00Z')),['close','low']]=[95.,90.]
    f.loc[f.date.eq(pd.Timestamp('2025-01-01T06:00Z')),['close','low']]=[94.,90.]
    got,_=completed_states(f,'dual');q=got.set_index('date')
    assert q.at[pd.Timestamp('2025-01-01T01:00Z'),'source_signal']==1
    assert q.at[pd.Timestamp('2025-01-01T05:00Z'),'target_weight']==1
    assert q.at[pd.Timestamp('2025-01-01T06:00Z'),'source_signal']==-1
    assert q.at[pd.Timestamp('2025-01-01T06:00Z'),'target_weight']==0
    assert not got.loc[got.clock.lt(pd.Timestamp('2025-01-01T00:00Z')),'target_weight'].any()


def test_unrenewed_365day_intent_expires_at_exact_boundary():
    f=data(405);stamp=pd.Timestamp('2025-01-01T01:00Z')
    f.loc[f.date.eq(stamp),'close']=110.
    got,_=completed_states(f,'dual');q=got.set_index('clock');expiry=stamp+pd.Timedelta(hours=1,days=365)
    assert q.at[expiry-pd.Timedelta(hours=1),'target_weight']==1
    assert q.at[expiry,'target_weight']==0


def test_quote_units_do_not_change_states_or_source_events():
    f=data();f.loc[f.date.eq(pd.Timestamp('2025-01-01T01:00Z')),'close']=110.
    a,_=completed_states(f,'dual');f[['open','high','low','close']]*=8
    b,_=completed_states(f,'dual')
    np.testing.assert_array_equal(a[['target_weight','source_signal']],b[['target_weight','source_signal']])


@pytest.mark.parametrize('fault',['gap','unknown','no_warmup'])
def test_reject_incomplete_input_instead_of_flat_fill(fault):
    f=data()
    if fault=='gap':f=f.drop(750)
    elif fault=='unknown':f.loc[750,'close']=np.nan
    else:f=f.loc[f.date.ge(pd.Timestamp('2025-01-01T00:00Z'))]
    with pytest.raises(ValueError):completed_states(f,'dual')
