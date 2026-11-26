"""Native Registry/factor_analysis on explicitly common known input dates."""
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

HERE=Path(__file__).resolve().parent;VIBE=Path('/Users/chrise/GitHub/Vibe-Trading/agent')
sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')
def ranks(x):
    order=sorted(range(len(x)),key=x.__getitem__);out=[0.]*len(x);i=0
    while i<len(x):
        j=i+1
        while j<len(x) and x[order[j]]==x[order[i]]:j+=1
        for k in range(i,j):out[order[k]]=(i+1+j)/2
        i=j
    return out
def correlation(a,b,rank=False):
    if rank:a,b=ranks(a),ranks(b)
    ma=math.fsum(a)/len(a);mb=math.fsum(b)/len(b)
    return math.fsum((x-ma)*(y-mb)for x,y in zip(a,b,strict=True))/math.sqrt(math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))

def main():
    for path,h in read(HERE/'implementation_freeze.json').items():assert sha(path)==h,path
    pre=read(HERE/'native_preflight.json');assert pre['passed_for_input_diagnostics']
    a=read(HERE/'input_admission.json');out=Path(a['out']);folder=HERE/'predictive';folder.mkdir(exist_ok=False)
    full_days=pd.date_range('2025-01-01','2026-08-30',tz='UTC',freq='D')
    assert len(full_days)==607
    names=['rsj','rsk','rkt','day_return','rv']
    full={k:pd.read_parquet(out/'features'/('native_factor_'+k+'.parquet')).reindex(full_days)for k in names}
    mask=pd.Series(True,index=full_days)
    for f in full.values():mask &=f.notna().all(axis=1)
    days=full_days[mask.to_numpy()];assert len(days)==605
    frames={k:f.loc[days]for k,f in full.items()}
    labels=pd.DataFrame(index=full_days,columns=a['pairs'],dtype=float);closes=labels.copy()
    handler=get_datahandler(Path(a['native_datadir']),'feather')
    for pair in a['pairs']:
        frame=handler.ohlcv_load(pair,'1h',candle_type=CandleType.FUTURES,fill_missing=False,drop_incomplete=False).set_index('date')
        labels[pair]=frame.open.reindex(full_days+pd.Timedelta(hours=25)).to_numpy()/frame.open.reindex(full_days+pd.Timedelta(hours=1)).to_numpy()-1
        closes[pair]=frame.close.reindex(full_days-pd.Timedelta(hours=1)).to_numpy()
    assert np.isfinite(labels).all().all()
    labels.to_csv(folder/'full607_returns.csv')
    for k,f in full.items():f.to_csv(folder/('full607_negative_'+k+'.csv'))
    save(folder/'mask_receipt.json',{'before_factor_api_outputs':True,'original_account_window_unchanged':True,
         'complete_label_dates':607,'common_observed_input_dates':605,'removed_unknown_input_dates':[str(d)for d in full_days[~mask.to_numpy()]],
         'selection_uses_future_returns':False,'dropped_returns_assumed_zero':False,
         'financial_full_input_admitted':pre['full_financial_input_admitted'],
         'diagnostics_conditional_on_known_inputs_not_complete_funded_returns':True})
    zoo=folder/'zoo/core';zoo.mkdir(parents=True)
    for name in names:
        meta=dict(id='core_intraday_'+name+'_view',nickname='Native signedvariation negative '+name,
                  theme=['reversal'if name=='day_return'else'volatility'],formula_latex='-'+name,
                  columns_required=['close'],extras_required=['native_'+name],universe=['crypto'],frequency=['1d'],
                  decay_horizon=1,min_warmup_bars=0,stage='research',
                  notes='Local isolated view of actual native IStrategy daily minuteVWAP feature; no globalRegistry edit, acceptedalpha, account or source51spot replication. Joint knowninput mask disclosed.')
        (zoo/(name+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+name)+'].copy()\n')
    registry=Registry(zoo_root=folder/'zoo');assert len(registry.list())==5
    owners=[Path(__file__),VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',VIBE/'src/tools/factor_analysis_tool.py',*zoo.glob('*.py')]
    save(folder/'owners.json',{str(p):sha(p)for p in owners})
    # Scope remains named and inspectable; these comparisons do not establish
    # independence from every factor or certify three new economic mechanisms.
    oldplan=read(HERE.parent/'spread_liquidity_native_20261114_v1/plan.json')
    oldpath=Path(oldplan['output_root'])/'EdgeLiquidity_1000_15bps/native_features.parquet'
    old=pd.read_parquet(oldpath)
    extra={k:old.pivot(index='decision_date',columns='pair',values=k).reindex(days)for k in ['edge30','amihud30','lowvol63']}
    residual_admission=read(HERE.parent/'residual_momentum_source_native_20261114_v1/input_admission.json')
    residual_path=Path(residual_admission['datadir']).parent/'native_signal.parquet';residual=pd.read_parquet(residual_path)
    extra['resmom14']=pd.DataFrame(residual.reindex(days-pd.Timedelta(hours=1)).to_numpy(),index=days,columns=residual.columns)
    dedup=[]
    for name in ['rsj','rsk','rkt']:
        peers={**{k:f for k,f in frames.items()if k!=name},**extra}
        for peer_name,peer in peers.items():
            shared=sorted(set(frames[name].columns)&set(peer.columns));values=[]
            for day in days:
                left=frames[name].loc[day,shared].to_numpy();right=peer.loc[day,shared].to_numpy();good=np.isfinite(left)&np.isfinite(right)
                if good.sum()>=5:values.append(abs(correlation(left[good].tolist(),right[good].tolist())))
            mean=math.fsum(values)/len(values)
            dedup.append({'candidate':name,'peer':peer_name,'shared_assets':len(shared),'observations':len(values),'mean_abs_pearson':mean,'duplicate_gt_99':mean>.99})
    save(folder/'deduplication.json',{'comparisons':dedup,'scope':'each3sourceviews vs4current peers,3oldnative6coinliquidity views and1old8coinresidual;not allRegistry','bindings':{str(p):sha(p)for p in [oldpath,residual_path]}})
    receipts=[];ic_count=group_cells=0;maximum_group_error=0.
    for name,factor in frames.items():
        view=registry.compute('core_intraday_'+name+'_view',{'close':closes.loc[days],'native_'+name:factor})
        pd.testing.assert_frame_equal(view,factor,check_exact=True)
        for split,subset in [('all_known',days),('calendar2025_known',days[days.year==2025]),('calendar2026_known',days[days.year==2026])]:
            directory=folder/name/split;directory.mkdir(parents=True)
            f=view.loc[subset];y=labels.loc[subset];f.to_csv(directory/'factor.csv');y.to_csv(directory/'returns.csv')
            result=json.loads(FactorAnalysisTool().execute(factor_csv=str(directory/'factor.csv'),return_csv=str(directory/'returns.csv'),output_dir=str(directory/'native'),n_groups=5))
            assert result['status']=='ok',result
            actual_ic=pd.read_csv(directory/'native/ic_series.csv',index_col=0).iloc[:,0].to_numpy()
            independent=[correlation(x.tolist(),z.tolist(),True)for x,z in zip(f.to_numpy(),y.to_numpy(),strict=True)]
            assert np.max(np.abs(actual_ic-independent))<1e-12
            group=pd.read_csv(directory/'native/group_equity.csv',index_col=0).to_numpy();running=[1.]*5
            for i,(scores,returns)in enumerate(zip(f.to_numpy().tolist(),y.to_numpy().tolist(),strict=True)):
                order=sorted(range(8),key=lambda j:(scores[j],j));cuts=[7*(g/5)for g in range(1,5)]
                members=[[j for rank,j in enumerate(order)if sum(rank>c for c in cuts)==g]for g in range(5)]
                for g in range(5):running[g]*=1+math.fsum(returns[j]for j in members[g])/len(members[g])
                maximum_group_error=max(maximum_group_error,max(abs(x-z)for x,z in zip(running,group[i],strict=True)))
            assert maximum_group_error<1e-9,maximum_group_error
            gate=result['ic_mean']>.03 and result['ir']>.5 and result['ic_positive_ratio']>.55
            receipts.append({'factor':name,'split':split,'observations':len(subset),'result':result,'positive_gate':gate})
            ic_count+=len(actual_ic);group_cells+=group.size
    status=[]
    for name in ['rsj','rsk','rkt']:
        numeric=all(r['positive_gate']for r in receipts if r['factor']==name)
        duplicate=any(r['duplicate_gt_99']for r in dedup if r['candidate']==name)
        status.append({'factor':name,'predictive_numeric_gate':numeric,'scoped_duplicate':duplicate,'full_input_admitted':pre['full_financial_input_admitted'],'financial_stage_admitted':numeric and not duplicate and pre['full_financial_input_admitted']})
    save(HERE/'predictive_results.json',{'actual_registry_views':5,'global_registry_modified':False,'actual_factor_analysis_calls':15,
         'unique_known_input_label_dates':len(days),'original_complete_label_dates':607,'unknown_dates_preserved':2,
         'ic_rows_including_full_split_overlap':ic_count,'independent_group_nav_cells':group_cells,
         'maximum_group_nav_error':maximum_group_error,'scoped_dedup_comparisons':len(dedup),'candidate_status':status,
         'receipts':receipts,'source_candidate_family_count':1,'new_accepted_alphas':0,'financial_accounts':0,'new_combinations':0,
         'conditional_diagnostics_are_not_full_account_returns':True,'net_account_returns':None,'independent_oos_certified':False})
    print('NATIVE FACTOR ANALYSIS15;',len(days),'known dates;',ic_count,'IC rows;',group_cells,'group cells;',status)

if __name__=='__main__':main()
