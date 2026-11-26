"""Clock, missingness, rank and cohort contract tests before financial outcomes."""
import json
from pathlib import Path
from zipfile import ZipFile
import numpy as np
import pandas as pd
import pytest
from Signals import owner
HERE=Path(__file__).resolve().parent
module=owner(HERE)

def fixture_archive(tmp_path,rows=None,day='2024-01-01'):
 rows=rows or [('2024-01-01 00:00:00',1),('2024-01-01 23:55:00',2)]
 frame=pd.DataFrame({'create_time':[a for a,b in rows],'symbol':'BTCUSDT',
  **{c:[b for a,b in rows]for c in module.RATIO_COLUMNS}})
 p=tmp_path/(day+'.zip')
 with ZipFile(p,'w')as z:z.writestr('metrics.csv',frame.to_csv(index=False))
 return {'symbol':'BTCUSDT','day':day,'path':str(p)}

def test_exact_daily_snapshot_ignores_input_row_order(tmp_path):
 r=fixture_archive(tmp_path,[('2024-01-01 23:55:00',2),('2024-01-01 00:00:00',1)])
 frame,diag=module.daily_positioning([r],'BTCUSDT')
 assert frame.iloc[0].eq(2).all()and not diag[0]['raw_rows_were_sorted']

def test_missing_exact_snapshot_is_unknown_not_nearest(tmp_path):
 r=fixture_archive(tmp_path,[('2024-01-01 23:50:00',2)])
 frame,diag=module.daily_positioning([r],'BTCUSDT')
 assert frame.iloc[0].isna().all()and not diag[0]['exact_snapshot_present']

@pytest.mark.parametrize('bad',[0,-1,np.nan,np.inf])
def test_invalid_snapshot_is_unknown_not_zero(tmp_path,bad):
 r=fixture_archive(tmp_path,[('2024-01-01 23:55:00',bad)])
 frame,_=module.daily_positioning([r],'BTCUSDT');assert frame.iloc[0].isna().all()

@pytest.mark.parametrize('fault',['duplicate','foreign_day','off_grid','missing_day'])
def test_ambiguous_archive_rejects(tmp_path,fault):
 r=fixture_archive(tmp_path)
 if fault=='duplicate':records=[r,r]
 elif fault=='missing_day':records=[r,dict(r,day='2024-01-03')]
 else:
  date='2024-01-02 23:55:00'if fault=='foreign_day'else'2024-01-01 23:54:00'
  records=[fixture_archive(tmp_path,[(date,2)])]
 with pytest.raises(ValueError):module.daily_positioning(records,'BTCUSDT')

def test_full_history_excludes_current_and_resolves_ties():
 days=pd.date_range('2024-01-01',periods=368,tz='UTC');s=pd.Series(np.ones(368),index=days)
 r=module.trailing_midrank(s);assert r.iloc[:365].isna().all()and r.iloc[365:].eq(.5).all()
 s.iloc[365]=.1;r=module.trailing_midrank(s);assert r.iloc[365]==0
 s.iloc[365]=2;r=module.trailing_midrank(s);assert r.iloc[365]==1

def test_unknown_in_full_reference_window_is_not_shorter_sample():
 s=pd.Series(np.arange(800.)+1,index=pd.date_range('2024-01-01',periods=800,tz='UTC'))
 s.iloc[100]=np.nan;r=module.trailing_midrank(s)
 assert r.iloc[365:466].isna().all()and r.iloc[466:].notna().all()

def test_future_perturbation_and_truncation_leave_prior_ranks_exact():
 s=pd.Series(np.random.default_rng(41).uniform(.5,3,800),index=pd.date_range('2024-01-01',periods=800,tz='UTC'))
 r=module.trailing_midrank(s);cut=600;changed=s.copy();changed.iloc[cut:]*=100
 pd.testing.assert_series_equal(r.iloc[:cut],module.trailing_midrank(changed).iloc[:cut],check_exact=True)
 pd.testing.assert_series_equal(r.iloc[:cut],module.trailing_midrank(s.iloc[:cut]),check_exact=True)

def test_previous_day_event_lasts_exactly30days_and_no_pre_account_inheritance():
 idx=pd.date_range('2024-12-01','2025-03-01',tz='UTC');r=pd.Series(.5,index=idx)
 r.loc['2024-12-15']=0;r.loc['2024-12-31']=.1
 d=module.monthly_opportunities(r,'2025-01-01','2025-02-28')
 assert d.index[0]==pd.Timestamp('2025-01-01',tz='UTC')and d.new_opportunity.sum()==1
 assert d.active.iloc[:30].all()and not d.active.iloc[30:].any()

def test_overlap_never_pyramids_and_unknown_does_not_extend_expiry():
 idx=pd.date_range('2024-12-01','2025-03-31',tz='UTC');r=pd.Series(.5,index=idx)
 r.loc['2024-12-31']=0;r.loc['2025-01-10']=0;r.loc['2025-01-11':'2025-02-28']=np.nan
 d=module.monthly_opportunities(r,'2025-01-01','2025-03-01')
 assert d.active.dtype==bool and d.active.iloc[:40].all()and not d.active.iloc[40:].any()
 assert not d.loc['2025-01-12':'2025-02-28','new_opportunity'].any()

def test_opportunity_does_not_use_same_day_ratio():
 r=pd.Series(.5,index=pd.date_range('2024-01-01','2025-02-28',tz='UTC'));r.loc['2025-01-15']=0
 d=module.monthly_opportunities(r,'2025-01-01','2025-02-28')
 assert not d.loc['2025-01-15','active']and d.loc['2025-01-16','active']
