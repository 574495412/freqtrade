"""Real input-clock invariants, not financial-strategy tests."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from OnchainInputs import (
    DailyHashSource, MetricClock, OnchainInputError,
    reconcile_hash_completion_snapshots, require_complete_metric_clock,
)

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[2]/'user_data/crypto_quant/onchain_hash_native_20261114_v1/v002'


def fixture():
    days = pd.date_range('2024-10-01', periods=110, freq='D', tz='UTC')
    return pd.DataFrame(dict(date=days, HashRate=np.arange(110, dtype=float)+100,
                             AssetEODCompletionTime=(days+pd.Timedelta(hours=28)).as_unit('ns').asi8//10**9))


def source(frame=None):
    return DailyHashSource(fixture() if frame is None else frame,
                           pd.Timestamp('2026-11-13T20:00Z'))


@pytest.mark.parametrize('fault', ['gap','duplicate','reverse','midday','nan','negative','missing_clock','milliseconds','preclose'])
def test_reject_unknown_or_invalid_daily_contract(fault):
    f=fixture()
    if fault=='gap':f=f.drop(40)
    elif fault=='duplicate':f=pd.concat([f,f.iloc[[40]]])
    elif fault=='reverse':f=f.iloc[::-1]
    elif fault=='midday':f.loc[40,'date']+=pd.Timedelta(hours=1)
    elif fault=='nan':f.loc[40,'HashRate']=np.nan
    elif fault=='negative':f.loc[40,'HashRate']=-1
    elif fault=='missing_clock':f['AssetEODCompletionTime']=f.AssetEODCompletionTime.astype(float);f.loc[40,'AssetEODCompletionTime']=np.nan
    elif fault=='milliseconds':f['AssetEODCompletionTime']*=1000
    elif fault=='preclose':f.loc[40,'AssetEODCompletionTime']=int(f.date.iloc[40].timestamp())
    with pytest.raises(OnchainInputError):source(f)


def test_all_sixty_observations_own_the_available_clock():
    f=fixture(); delayed=f.date.iloc[80]+pd.Timedelta(days=2)
    f.loc[22,'AssetEODCompletionTime']=int(delayed.timestamp())
    result=source(f).features(MetricClock.COMPLETION_MODEL)
    assert result.iloc[0].available_at==delayed
    assert result.loc[result.metric_day.eq(f.date.iloc[81]),'available_at'].iloc[0]==f.date.iloc[82]+pd.Timedelta(hours=4)
    for row in result.itertuples():
        i=f.index[f.date.eq(row.metric_day)][0]; values=f.HashRate.iloc[i-59:i+1].tolist()
        assert row.hash_sma60==pytest.approx(math.fsum(values)/60,abs=1e-12)
        assert row.hash_sma30==pytest.approx(math.fsum(values[-30:])/30,abs=1e-12)


def test_exact_completion_boundary_default_snapshot_and_delay():
    s=source(); f=s.features(MetricClock.COMPLETION_MODEL); stamp=f.available_at.iloc[0]
    h=pd.DatetimeIndex([stamp-pd.Timedelta(nanoseconds=1),stamp])
    assert s.asof_hours(h,clock=MetricClock.COMPLETION_MODEL).status.tolist()==['UNAVAILABLE','KNOWN']
    assert s.asof_hours(h).status.eq('UNAVAILABLE').all()
    delayed=s.asof_hours(pd.DatetimeIndex([stamp+pd.Timedelta(hours=24)]),clock=MetricClock.COMPLETION_MODEL,delay_hours=24)
    assert delayed.metric_day.iloc[0]==f.metric_day.iloc[0]


def test_real_snapshot_max_clock_and_scalar_averages():
    f=pd.read_parquet(OUT/'hash_daily.parquet'); s=source(f)
    feature=s.features(MetricClock.COMPLETION_MODEL)
    for row in feature.itertuples():
        i=f.index[f.date.eq(row.metric_day)][0]; block=f.iloc[i-59:i+1]
        assert row.available_at==pd.Timestamp(int(max(block.AssetEODCompletionTime)),unit='s',tz='UTC')
        assert row.hash_sma30==pytest.approx(math.fsum(block.HashRate.tolist()[-30:])/30,rel=2e-15)
        assert row.hash_sma60==pytest.approx(math.fsum(block.HashRate.tolist())/60,rel=2e-15)


@pytest.mark.parametrize('cutoff',['2025-01-01','2025-05-04','2026-01-01','2026-07-01'])
def test_real_future_perturbation_and_truncation_leave_prior_features(cutoff):
    f=pd.read_parquet(OUT/'hash_daily.parquet'); end=pd.Timestamp(cutoff,tz='UTC')
    h=pd.date_range(end-pd.Timedelta(days=10),end,freq='h'); original=source(f).asof_hours(h,clock=MetricClock.COMPLETION_MODEL)
    changed=f.copy(); changed.loc[changed.date.ge(end),'HashRate']*=19
    altered=source(changed).asof_hours(h,clock=MetricClock.COMPLETION_MODEL)
    truncated=source(f.loc[f.date.lt(end)]).asof_hours(h,clock=MetricClock.COMPLETION_MODEL)
    pd.testing.assert_frame_equal(original,altered,check_exact=True)
    pd.testing.assert_frame_equal(original,truncated,check_exact=True)


def test_uniform_units_do_not_change_dimensionless_ratio_or_inputs():
    f=fixture(); s=source(f); f.loc[0,'HashRate']=99999
    assert s.frame.HashRate.iloc[0]==100
    changed=s.frame.copy(); changed['HashRate']*=1e-6
    np.testing.assert_allclose(s.features().hash_ratio,source(changed).features().hash_ratio,rtol=0,atol=5e-16)


def test_reconciliation_keeps_original_nulls_and_only_equal_raw_values():
    a=pd.DataFrame([dict(date='2025-01-01',HashRate='123.00000000000000000001',AssetEODCompletionTime=None)])
    b=a.copy();b['AssetEODCompletionTime']='1735790400'
    result=reconcile_hash_completion_snapshots(a,b)
    assert result.current_completion_missing.iloc[0] and pd.isna(result.completion_current.iloc[0])
    assert result.AssetEODCompletionTime.iloc[0]==1735790400
    b['HashRate']='123.00000000000000000002'
    with pytest.raises(OnchainInputError,match='Conflicting'):reconcile_hash_completion_snapshots(a,b)


@pytest.mark.parametrize('fault',['both_missing','duplicate_current','duplicate_archive','unknown_hash'])
def test_reconciliation_never_creates_a_missing_clock_or_value(fault):
    a=fixture().iloc[:2].copy();b=a.copy()
    if fault=='duplicate_current':a=pd.concat([a,a.iloc[[0]]])
    elif fault=='duplicate_archive':b=pd.concat([b,b.iloc[[0]]])
    elif fault=='unknown_hash':a.loc[0,'HashRate']=np.nan
    else:
        a['AssetEODCompletionTime']=None;b['AssetEODCompletionTime']=None
        with pytest.raises(OnchainInputError):source(reconcile_hash_completion_snapshots(a,b))
        return
    with pytest.raises(OnchainInputError):reconcile_hash_completion_snapshots(a,b)


def test_actual_frozen_window_fails_both_completion_models():
    for lag,count in [(0,46),(24,70)]:
        f=pd.read_parquet(OUT/f'input_clock_completion_model_{lag}.parquet')
        assert len(f)==14592 and f.status.eq('STALE').sum()==count
        assert not f.snapshot_observed_before_decision.any()
        with pytest.raises(OnchainInputError,match=str(count)):require_complete_metric_clock(f)
    strict=pd.read_parquet(OUT/'input_clock_snapshot_observed_0.parquet')
    assert strict.status.eq('UNAVAILABLE').all()


@pytest.mark.parametrize('fault',['naive','duplicate','reverse'])
def test_reject_bad_decision_clock(fault):
    hours=pd.date_range('2025-01-01',periods=3,freq='h',tz='UTC')
    if fault=='naive':hours=hours.tz_localize(None)
    elif fault=='duplicate':hours=hours.append(hours[:1])
    else:hours=hours[::-1]
    with pytest.raises(OnchainInputError):source().asof_hours(hours)
