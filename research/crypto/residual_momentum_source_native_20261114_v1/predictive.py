"""Actual isolated Registry and native factor_analysis before financial runs."""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import boot

boot()
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType

HERE=Path(__file__).resolve().parent
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading/agent')
sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')
def ranks(values):
    order=sorted(range(len(values)),key=values.__getitem__);out=[0.]*len(values);i=0
    while i<len(values):
        j=i+1
        while j<len(values) and values[order[j]]==values[order[i]]:j+=1
        for k in range(i,j):out[order[k]]=(i+1+j)/2
        i=j
    return out
def correlation(a,b,rank=False):
    if rank:a,b=ranks(a),ranks(b)
    ma=math.fsum(a)/len(a);mb=math.fsum(b)/len(b)
    return math.fsum((x-ma)*(y-mb)for x,y in zip(a,b,strict=True))/math.sqrt(math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))

def main():
    assert read(HERE/'native_preflight.json')['passed']
    for path,h in read(HERE/'implementation_freeze.json').items():assert sha(path)==h,path
    a=read(HERE/'input_admission.json');out=Path(a['datadir']).parent
    folder=HERE/'predictive';folder.mkdir(exist_ok=False)
    close=pd.read_parquet(out/'native_close.parquet')
    signal=pd.read_parquet(out/'native_signal.parquet');raw=pd.read_parquet(out/'native_raw.parquet')
    lowvol=-close.pct_change(fill_method=None).rolling(720,min_periods=684).std()
    all_days=pd.date_range('2025-01-01','2026-08-31 23:00',freq='168h',tz='UTC')
    days=all_days[all_days+pd.Timedelta(hours=169)<=pd.Timestamp('2026-08-31 23:00',tz='UTC')]
    assert len(days)==86
    previous=days-pd.Timedelta(hours=1)
    frames={name:pd.DataFrame(frame.reindex(previous).to_numpy(),index=days,columns=close.columns)
            for name,frame in [('resmom14',signal),('raw14',raw),('lowvol30',lowvol)]}
    labels=pd.DataFrame(index=days,columns=close.columns,dtype=float)
    handler=get_datahandler(Path(a['datadir']),'feather')
    for pair in close.columns:
        frame=handler.ohlcv_load(pair,'1h',candle_type=CandleType.FUTURES,fill_missing=False,drop_incomplete=False).set_index('date')
        begin=frame.open.reindex(days+pd.Timedelta(hours=1)).to_numpy()
        end=frame.open.reindex(days+pd.Timedelta(hours=169)).to_numpy()
        labels[pair]=end/begin-1
    assert np.isfinite(labels).all().all() and all(np.isfinite(f).all().all()for f in frames.values())
    panel_close=pd.DataFrame(close.reindex(previous).to_numpy(),index=days,columns=close.columns)
    zoo=folder/'zoo/core';zoo.mkdir(parents=True)
    for name in frames:
        meta=dict(id='core_native_'+name+'_view',nickname='Native residual source '+name+' input view',
                  theme=['volatility'if name.startswith('lowvol')else'momentum'],formula_latex=name,
                  columns_required=['close'],extras_required=['native_'+name],universe=['crypto'],
                  frequency=['1h'],decay_horizon=7,min_warmup_bars=0,stage='research',
                  notes='Local pre-financial isolated view of actual native IStrategy/DataProvider scores; source720/336 windows and joint input admission owned by freqtrade.data.residual_momentum. Not new accepted alpha or upstream Freqtrade factor registry.')
        (zoo/(name+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+name)+'].copy()\n')
    registry=Registry(zoo_root=folder/'zoo')
    assert len(registry.list())==3
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',VIBE/'src/tools/factor_analysis_tool.py',Path(__file__),*zoo.glob('*.py')]
    save(folder/'owners.json',{str(p):sha(p)for p in owners})
    # Scoped dedup: same-date raw and lowvol controls and three older native
    # liquidity views. Report exact shared assets/support, never global novelty.
    peers={k:f for k,f in frames.items()if k!='resmom14'}
    old_plan=read(HERE.parent/'spread_liquidity_native_20261114_v1/plan.json')
    old_path=Path(old_plan['output_root'])/'EdgeLiquidity_1000_15bps/native_features.parquet'
    old=pd.read_parquet(old_path)
    for key in ['edge30','amihud30','lowvol63']:
        peers[key]=old.pivot(index='decision_date',columns='pair',values=key).reindex(days)
    dedup=[]
    for name,peer in peers.items():
        common=sorted(set(peer.columns)&set(frames['resmom14'].columns));correlations=[]
        for day in days:
            left=frames['resmom14'].loc[day,common].to_numpy();right=peer.loc[day,common].to_numpy()
            good=np.isfinite(left)&np.isfinite(right)
            if good.sum()>=5:correlations.append(abs(correlation(left[good].tolist(),right[good].tolist())))
        dedup.append({'peer':name,'shared_assets':len(common),'observations':len(correlations),
                      'mean_abs_pearson':math.fsum(correlations)/len(correlations),'duplicate_gt_99':math.fsum(correlations)/len(correlations)>.99})
    save(folder/'deduplication.json',{'scope':'2same-eight controls plus3older native-six liquidity views only; not every Registry factor','old_feature_binding':{'path':str(old_path),'sha256':sha(old_path)},'comparisons':dedup})
    receipts=[];ic_rows=group_cells=0;max_group_error=0.
    for name,factor in frames.items():
        view=registry.compute('core_native_'+name+'_view',{'close':panel_close,'native_'+name:factor})
        pd.testing.assert_frame_equal(view,factor,check_exact=True)
        for split,subset in [('all',days),('calendar2025',days[days.year==2025]),('calendar2026_through_aug',days[days.year==2026])]:
            target=folder/name/split;target.mkdir(parents=True)
            f=view.loc[subset];y=labels.loc[subset]
            f.to_csv(target/'factor.csv');y.to_csv(target/'returns.csv')
            value=json.loads(FactorAnalysisTool().execute(factor_csv=str(target/'factor.csv'),return_csv=str(target/'returns.csv'),output_dir=str(target/'native'),n_groups=4))
            assert value['status']=='ok',value
            observed=pd.read_csv(target/'native/ic_series.csv',index_col=0).iloc[:,0].to_numpy()
            expected=[correlation(x.tolist(),z.tolist(),True)for x,z in zip(f.to_numpy(),y.to_numpy(),strict=True)]
            assert np.max(np.abs(observed-expected))<1e-12
            group=pd.read_csv(target/'native/group_equity.csv',index_col=0).to_numpy();running=[1.]*4
            for i,(scores,returns)in enumerate(zip(f.to_numpy().tolist(),y.to_numpy().tolist(),strict=True)):
                order=sorted(range(8),key=lambda j:(scores[j],j))
                for g in range(4):running[g]*=1+math.fsum(returns[j]for j in order[2*g:2*g+2])/2
                max_group_error=max(max_group_error,max(abs(x-z)for x,z in zip(running,group[i],strict=True)))
            assert max_group_error<1e-10,max_group_error
            ic_rows+=len(observed);group_cells+=group.size
            gate=value['ic_mean']>.03 and value['ir']>.5 and value['ic_positive_ratio']>.55
            receipts.append({'factor':name,'split':split,'observations':len(subset),'result':value,'positive_gate':gate})
    eligible=all(r['positive_gate']for r in receipts if r['factor']=='resmom14')and not any(d['duplicate_gt_99']for d in dedup)
    save(HERE/'predictive_results.json',{'actual_registry_views':3,'global_registry_modified':False,
         'actual_factor_analysis_calls':len(receipts),'unique_decision_dates':86,'assets':8,
         'independent_ic_rows_including_reused_splits':ic_rows,'independent_group_nav_cells':group_cells,
         'maximum_group_nav_error':max_group_error,'label':'gross nativefutures01:00-to+168h01:00OPEN simple return; nofees/funding; nonoverlapping; completeendpointsonly',
         'source_factor_admitted_to_financial_stage':eligible,'receipts':receipts,
         'historical_funded_accounts':0,'accepted_new_alphas':0,'independent_oos_certified':False,
         'group_curves_are_funded_returns':False,'deduplication':dedup})
    print('Actual FA calls',len(receipts),'IC rows',ic_rows,'group cells',group_cells,'financialstage admitted',eligible)

if __name__=='__main__':main()
