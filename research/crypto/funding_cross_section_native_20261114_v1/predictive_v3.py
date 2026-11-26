"""Pre-financial native Registry/factor_analysis diagnostic, never account PnL."""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading/agent')
sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ranks(values):
    order=sorted(range(len(values)),key=values.__getitem__);out=[0.]*len(values);i=0
    while i<len(values):
        j=i+1
        while j<len(values) and values[order[j]]==values[order[i]]:j+=1
        for k in range(i,j):out[order[k]]=(i+1+j)/2
        i=j
    return out


def scalar_ic(a,b):
    a=ranks(a);b=ranks(b);ma=math.fsum(a)/len(a);mb=math.fsum(b)/len(b)
    return math.fsum((x-ma)*(y-mb) for x,y in zip(a,b,strict=True))/math.sqrt(math.fsum((x-ma)**2 for x in a)*math.fsum((x-mb)**2 for x in b))


def main():
    admitted=json.loads((HERE/'input_admission.json').read_text());out=Path(admitted['funding_events']['path']).parent
    if not json.loads((HERE/'native_preflight.json').read_text())['passed_for_input_diagnostics']:raise ValueError('Native input preflight required')
    directory=HERE/'predictive_v3';directory.mkdir(exist_ok=False)
    protocol=dict(before_new_predictive_outputs=True,native_funded_accounts_started=False,
        factor='negative trailing7completedUTCday mean of actual daily funding sums',
        observations='source every5calendar-days,2023-01-09 phase; decision within2025Jan1--2026Aug24',
        labels='actual native perp 01:00 OPEN to7days-later01:00 OPEN simple return',
        source_difference='Original H5 uses daily spot log returns, all daily observations; here native perp simple returns on source decision grid',
        missing='retain TIA 2026Jun24 UNKNOWN and7following unknown factor days; no zero replacement',
        groups=3,positive_gate=dict(ic_mean_gt=.03,ir_gt=.5,positive_ratio_gt=.55),
        dependence='Seven-day labels sampled everyfive days overlap; IR descriptive,not independent significance or OOS',
        financial_input_admission=admitted['passed'],financial_returns=None)
    (directory/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    original=pd.read_parquet(out/'native_factor.parquet')
    days=pd.date_range('2025-01-01','2026-08-24',tz='UTC',freq='D')
    anchor=pd.Timestamp('2023-01-09',tz='UTC');days=days[[int((d-anchor).days)%5==0 for d in days]]
    factor=original.reindex(days);labels=pd.DataFrame(index=days,columns=factor.columns,dtype=float)
    close=labels.copy()
    for pair in factor.columns:
        sym=pair.split('/')[0]
        frame=pd.read_feather(out/f'candles/futures/{sym}_USDT_USDT-1h-futures.feather').set_index('date')
        first=frame.open.reindex(days+pd.Timedelta(hours=1)).to_numpy()
        last=frame.open.reindex(days+pd.Timedelta(days=7,hours=1)).to_numpy()
        labels[pair]=last/first-1
        close[pair]=frame.close.reindex(days-pd.Timedelta(hours=1)).to_numpy()
    if not np.isfinite(labels).all().all():raise ValueError('Native horizon label missing')
    zoo=directory/'zoo/core';zoo.mkdir(parents=True)
    meta=dict(id='core_crypto_funding7_view',nickname='Native funding7 input view',theme=['leverage'],
        formula_latex=r'-Mean_7(Sum_actual_funding_events_in_completed_UTC_day)',
        columns_required=['close'],extras_required=['native_funding7'],universe=['crypto'],frequency=['1d'],
        decay_horizon=7,min_warmup_bars=0,stage='research',
        notes='Local isolated diagnostic view;actual IStrategy input calculation owns7day support and missing event mask; no accepted factor/global Registry edit or funded account claim.')
    (zoo/'crypto_funding7_view.py').write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel["native_funding7"].copy()\n')
    registry=Registry(zoo_root=directory/'zoo')
    exposed=registry.compute('core_crypto_funding7_view',{'close':close,'native_funding7':factor})
    pd.testing.assert_frame_equal(exposed,factor,check_exact=True)
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',VIBE/'src/tools/factor_analysis_tool.py',zoo/'crypto_funding7_view.py',Path(__file__)]
    (directory/'owners.json').write_text(json.dumps({str(p):sha(p) for p in owners},indent=2)+'\n')
    receipts=[];ic_rows=0;group_cells=0;maxerror=0.
    for name,subset in [('all',days),('calendar2025',days[days.year==2025]),('calendar2026_through_aug24',days[days.year==2026])]:
        folder=directory/name;folder.mkdir();f=exposed.loc[subset];y=labels.loc[subset]
        f.to_csv(folder/'factor.csv');y.to_csv(folder/'returns.csv')
        value=json.loads(FactorAnalysisTool().execute(factor_csv=str(folder/'factor.csv'),return_csv=str(folder/'returns.csv'),output_dir=str(folder/'native'),n_groups=3))
        if value['status']!='ok':raise ValueError(value)
        ic=pd.read_csv(folder/'native/ic_series.csv',index_col=0).iloc[:,0].to_numpy()
        expected=[];running=[1.,1.,1.]
        group=pd.read_csv(folder/'native/group_equity.csv',index_col=0).to_numpy()
        for i,(day,scores) in enumerate(f.iterrows()):
            valid=scores.notna() & y.loc[day].notna();a=scores.loc[valid].tolist();b=y.loc[day,valid].tolist()
            expected.append(scalar_ic(a,b))
            order=sorted(range(len(a)),key=lambda j:(a[j],j));n=len(a)
            # qcut on first-ranked integers: boundaries at the linear1/3 and2/3 quantiles.
            cuts=[(n-1)*((g/3*100)/100) for g in [1,2]]
            members=[[j for k,j in enumerate(order) if (0 if k<=cuts[0] else 1 if k<=cuts[1] else 2)==g] for g in range(3)]
            for g in range(3):running[g]*=1+math.fsum(b[j] for j in members[g])/len(members[g])
            error=max(abs(a-b) for a,b in zip(running,group[i],strict=True));maxerror=max(maxerror,error)
        if len(ic)!=len(expected) or np.max(np.abs(ic-expected))>1e-12 or maxerror>1e-10:raise ValueError('Independent IC/group audit')
        receipts.append(dict(split=name,result=value,positive_gate=value['ic_mean']>.03 and value['ir']>.5 and value['ic_positive_ratio']>.55))
        ic_rows+=len(ic);group_cells+=group.size
    result=dict(actual_registry_views=1,global_registry_modified=False,actual_factor_analysis_calls_this_attempt=3,actual_factor_analysis_calls_including_preserved_attempts=5,unique_raw_factor_diagnostics=3,independent_bin_audit_percentile_roundoff_corrected=True,
        independent_ic_rows=ic_rows,independent_group_nav_cells=group_cells,maximum_group_nav_error=maxerror,
        unique_decision_dates=len(days),assets=29,unknown_factor_cells=int(factor.isna().sum().sum()),
        native_funded_accounts=0,source_returns_reproduced=False,independent_oos_certified=False,
        protocol_sha256=sha(directory/'protocol.json'),owners_sha256=sha(directory/'owners.json'),receipts=receipts)
    (HERE/'predictive_diagnostics_v3.json').write_text(json.dumps(result,indent=2)+'\n')
    print('NATIVE FA COMPLETE:3 calls;',len(days),'decisiondates;',ic_rows,'IC rows;',group_cells,'group NAV cells')


if __name__=='__main__':main()
