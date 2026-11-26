"""Post-run gross predictive diagnostics via Vibe Registry/factor_analysis.

These local Registry views do not generate the Freqtrade strategy's scores or
retroactively qualify its admission. Native Backtesting remains account owner.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading/agent')
sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scalar_ranks(values):
    ordered=sorted(range(len(values)),key=values.__getitem__)
    ranks=[0.]*len(values)
    pos=0
    while pos<len(values):
        end=pos+1
        while end<len(values) and values[ordered[end]]==values[ordered[pos]]:end+=1
        for j in range(pos,end):ranks[ordered[j]]=(pos+1+end)/2
        pos=end
    return ranks


def correlation(left,right):
    a=scalar_ranks(left);b=scalar_ranks(right)
    ac=math.fsum(a)/len(a);bc=math.fsum(b)/len(b)
    numerator=math.fsum((x-ac)*(y-bc) for x,y in zip(a,b,strict=True))
    return numerator/math.sqrt(math.fsum((x-ac)**2 for x in a)*math.fsum((x-bc)**2 for x in b))


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    directory=HERE/'predictive';directory.mkdir(exist_ok=False)
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',VIBE/'src/tools/factor_analysis_tool.py']
    dates=pd.date_range(plan['start'],'2026-08-30T00:00:00Z',freq='D')
    native=pd.read_parquet(out/'EdgeLiquidity_1000_15bps/native_features.parquet')
    labels=pd.DataFrame(index=dates,columns=plan['pairs'],dtype=float)
    close=labels.copy()
    for pair in plan['pairs']:
        symbol=pair.split('/')[0]
        prices=pd.read_feather(Path(plan['datadir'])/f'futures/{symbol}_USDT_USDT-1h-futures.feather').set_index('date').open
        daily=pd.read_feather(Path(plan['datadir'])/f'futures/{symbol}_USDT_USDT-1d-futures.feather').set_index('date').close
        begin=prices.reindex(dates+pd.Timedelta(hours=1)).to_numpy()
        end=prices.reindex(dates+pd.Timedelta(days=1,hours=1)).to_numpy()
        labels[pair]=end/begin-1
        close[pair]=daily.reindex(dates-pd.Timedelta(days=1)).to_numpy()
    if not np.isfinite(labels).all().all():raise ValueError('Complete label clock gap')
    zoo=directory/'zoo/core';zoo.mkdir(parents=True)
    identifiers=[]
    for field,theme in [('edge30','liquidity'),('amihud30','liquidity'),('lowvol63','volatility')]:
        identifier='core_native_'+field+'_view';identifiers.append(identifier)
        meta=dict(id=identifier,nickname='Native Freqtrade '+field+' diagnostic view',theme=[theme],
            formula_latex=field,columns_required=['close'],extras_required=['native_'+field],
            universe=['crypto'],frequency=['1d'],decay_horizon=1,min_warmup_bars=0,stage='research',
            notes='Local diagnostic view of actual native feature table. No global Registry mutation, new estimator, sign/window change, account engine or promotion. Underlying source/data warmup owned by frozen native strategy.')
        (zoo/('native_'+field+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+field)+'].copy()\n')
    registry=Registry(zoo_root=directory/'zoo')
    if sorted(registry.list())!=sorted(identifiers):raise ValueError(registry.health())
    manifest={str(p):sha(p) for p in [*owners,*zoo.glob('*.py'),Path(__file__)]}
    (directory/'owner_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    raw={field:native.pivot(index='decision_date',columns='pair',values=field).reindex(index=dates,columns=plan['pairs']) for field in ['edge30','amihud30','lowvol63']}
    panel={'close':close,**{'native_'+field:value for field,value in raw.items()}}
    receipts=[];ic_checks=0;group_checks=0;occupancy={}
    for field,reference in raw.items():
        factor=registry.compute('core_native_'+field+'_view',panel)
        pd.testing.assert_frame_equal(reference,factor,check_exact=True)
        order=[sorted(plan['pairs'],key=lambda p:(float(row[p]),p)) for _day,row in factor.iterrows()]
        occupancy[field]={'long_days':{p:sum(row[-1]==p for row in order) for p in plan['pairs']},
            'short_days':{p:sum(row[0]==p for row in order) for p in plan['pairs']},
            'daily_pair_target_changes':sum(a[0]!=b[0] or a[-1]!=b[-1] for a,b in zip(order[:-1],order[1:],strict=True))}
        for name,subset in [('all',dates),('calendar2025',dates[dates.year==2025]),('calendar2026_through_aug30',dates[dates.year==2026])]:
            folder=directory/field/name;folder.mkdir(parents=True)
            f=factor.loc[subset];y=labels.loc[subset]
            f.to_csv(folder/'factor.csv');y.to_csv(folder/'returns.csv')
            result=json.loads(FactorAnalysisTool().execute(factor_csv=str(folder/'factor.csv'),return_csv=str(folder/'returns.csv'),output_dir=str(folder/'native'),n_groups=3))
            if result['status']!='ok':raise ValueError(result)
            ic=pd.read_csv(folder/'native/ic_series.csv',index_col=0).iloc[:,0].to_numpy()
            independent=[correlation(a,b) for a,b in zip(f.to_numpy().tolist(),y.to_numpy().tolist(),strict=True)]
            if np.max(np.abs(ic-independent))>1e-12:raise ValueError('Independent Spearman mismatch')
            group=pd.read_csv(folder/'native/group_equity.csv',index_col=0).to_numpy()
            running=[1.,1.,1.]
            for i,(scores,returns) in enumerate(zip(f.to_numpy().tolist(),y.to_numpy().tolist(),strict=True)):
                ranked=sorted(range(6),key=lambda j:(scores[j],j))
                for g in range(3):running[g]*=1+math.fsum(returns[j] for j in ranked[g*2:g*2+2])/2
                if max(abs(a-b) for a,b in zip(running,group[i],strict=True))>1e-11:raise ValueError('Independent grouped NAV mismatch')
            ic_checks+=len(ic);group_checks+=group.size
            receipts.append(dict(factor=field,split=name,result=result,
                positive_gate=result['ic_mean']>.03 and result['ir']>.5 and result['ic_positive_ratio']>.55))
    result=dict(actual_registry_views=3,global_registry_modified=False,actual_factor_analysis_calls=len(receipts),
        local_cross_project_diagnostic=True,freqtrade_upstream_feature=False,
        run_after_frozen_native_accounts=True,source_sign_or_window_changed=False,
        label='gross 01:00-to-next01:00 futures OPEN return; no fees/funding; 607 full daily labels',
        local_group_count=3,minimum_native_ic_assets=5,observed_assets=6,
        independent_ic_rows=ic_checks,independent_group_nav_cells=group_checks,
        independent_oos_certified=False,group_curves_are_funded_account_returns=False,
        receipts=receipts,occupancy=occupancy,owner_manifest_sha256=sha(directory/'owner_manifest.json'))
    (HERE/'predictive_diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
    print('COMPLETE',len(receipts),'actual native FA calls;',ic_checks,'IC rows;',group_checks,'group NAV cells')


if __name__=='__main__':main()
