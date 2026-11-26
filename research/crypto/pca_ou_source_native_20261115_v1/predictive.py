"""Actual Registry/FactorAnalysisTool plus independent rank/quantile checks."""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import HERE, ROOT, boot

boot()
VIBE=ROOT.parent/'Vibe-Trading/agent';sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,r):Path(p).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')


def rank(values):
    order=sorted(range(len(values)),key=values.__getitem__);result=[0.]*len(values);i=0
    while i<len(order):
        end=i+1
        while end<len(order)and values[order[end]]==values[order[i]]:end+=1
        for at in range(i,end):result[order[at]]=(i+end+1)/2
        i=end
    return result


def correlation(a,b,*,ranked=False):
    if ranked:a,b=rank(a),rank(b)
    ma,mb=math.fsum(a)/len(a),math.fsum(b)/len(b)
    num=math.fsum((x-ma)*(y-mb)for x,y in zip(a,b,strict=True))
    den=math.sqrt(math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))
    return num/den if den else float('nan')


def audit(f,y,directory):
    observed=pd.read_csv(directory/'ic_series.csv',index_col=0).iloc[:,0].to_numpy()
    group=pd.read_csv(directory/'group_equity.csv',index_col=0).to_numpy()
    expected=[];running=[1.]*5;maximum=0.
    for i,(a,b)in enumerate(zip(f.to_numpy(),y.to_numpy(),strict=True)):
        good=np.isfinite(a)&np.isfinite(b);left,right=a[good].tolist(),b[good].tolist()
        assert len(left)>=20
        expected.append(correlation(left,right,ranked=True))
        order=sorted(range(len(left)),key=lambda j:(left[j],j));buckets=[[]for _ in range(5)]
        for pos,j in enumerate(order):
            g=max(0,min(4,math.ceil(pos*5/(len(left)-1))-1));buckets[g].append(right[j])
        for g in range(5):running[g]*=1+math.fsum(buckets[g])/len(buckets[g])
        delta=max(abs(a-b)for a,b in zip(running,group[i],strict=True));maximum=max(maximum,delta)
        np.testing.assert_allclose(running,group[i],rtol=1e-10,atol=1e-10)
    error=float(np.max(np.abs(observed-np.asarray(expected))));assert error<1e-12
    mean=math.fsum(expected)/len(expected)
    sd=math.sqrt(math.fsum((v-mean)**2 for v in expected)/(len(expected)-1))
    return dict(raw_ic_mean=mean,raw_ic_std=sd,raw_ir=mean/sd,
        raw_positive_ratio=sum(v>0 for v in expected)/len(expected),
        independent_ic_rows=len(expected),independent_group_cells=group.size,
        max_ic_error=error,max_group_nav_error=maximum)


def main():
    pre=read(HERE/'native_preflight.json');assert pre['passed']
    for path,digest in read(HERE/'implementation_freeze.json').items():assert sha(path)==digest,path
    out=Path(pre['factor_output']);protocol=read(HERE/'protocol.json')
    folder=HERE/'predictive';folder.mkdir(exist_ok=False)
    names=protocol['factor_diagnostics']['scores']+protocol['factor_diagnostics']['controls']
    frames={n:pd.read_parquet(out/('native_'+n+'.parquet'))for n in names}
    mask=frames['pca_ou_reversal'].notna()
    frames={n:f.where(mask)for n,f in frames.items()}
    price=pd.read_parquet(out/'native_close.parquet')
    zoo=folder/'zoo/core';zoo.mkdir(parents=True)
    for name in names:
        meta=dict(id='core_native_'+name+'_view',nickname='PCA OU '+name+' diagnostic',
            theme=['volatility'if name.startswith('lowvol')else'momentum'],formula_latex=name,
            columns_required=['close'],extras_required=['native_'+name],universe=['crypto'],
            frequency=['1h'],decay_horizon=1,min_warmup_bars=240,stage='research',
            notes='Isolated native diagnostic view,not global acceptedfactor. Metadata onebar;explicit1h/24hforwardlabels are not a validated holding period. Fixed40localuniverse,source2PC240price239logreturn OU math,not source account or historicalmarketcap replication.')
        (zoo/(name+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+name)+'].copy()\n')
    registry=Registry(zoo_root=folder/'zoo');assert len(registry.list())==4
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',VIBE/'src/tools/factor_analysis_tool.py',
        HERE/'predictive.py',HERE/'protocol.json',*zoo.glob('*.py')]
    save(HERE/'predictive_implementation_freeze.json',{str(p):sha(p)for p in owners})
    dedup=[]
    for peer in names[1:]:
        values=[]
        for a,b in zip(frames['pca_ou_reversal'].to_numpy(),frames[peer].to_numpy(),strict=True):
            good=np.isfinite(a)&np.isfinite(b)
            v=correlation(a[good].tolist(),b[good].tolist())
            if math.isfinite(v):values.append(abs(v))
        mean=math.fsum(values)/len(values)
        dedup.append(dict(peer=peer,observations=len(values),mean_abs_pearson=mean,duplicate_ge99=mean>=.99))
    receipts=[]
    for horizon in [1,24]:
        labels=pd.read_parquet(out/f'native_labels_{horizon}h.parquet');days=labels.index
        current_price=pd.DataFrame(price.reindex(days-pd.Timedelta(hours=1)).to_numpy(),index=days,columns=price.columns)
        for name in names:
            factor=frames[name].reindex(days)
            native=registry.compute('core_native_'+name+'_view',{'close':current_price,'native_'+name:factor})
            pd.testing.assert_frame_equal(native,factor,check_exact=True)
            for split,subset in [('all',days),('calendar2025',days[days.year==2025]),('calendar2026_through_aug',days[days.year==2026])]:
                target=folder/f'{horizon}h'/name/split;target.mkdir(parents=True)
                f,y=native.loc[subset],labels.loc[subset]
                f.to_csv(target/'factor.csv');y.to_csv(target/'returns.csv')
                response=json.loads(FactorAnalysisTool().execute(factor_csv=str(target/'factor.csv'),return_csv=str(target/'returns.csv'),
                    output_dir=str(target/'native'),n_groups=5))
                assert response['status']=='ok',response
                independent=audit(f,y,target/'native')
                gate=independent['raw_ic_mean']>.03 and independent['raw_ir']>.5 and independent['raw_positive_ratio']>.55
                row=dict(horizon_hours=horizon,factor=name,split=split,observations=len(subset),native=response,
                    independent=independent,positive_gate=gate)
                receipts.append(row)
                save(HERE/'predictive_progress.json',dict(actual_calls=len(receipts),receipts=receipts))
                print(horizon,name,split,independent['raw_ic_mean'],independent['raw_ir'],independent['raw_positive_ratio'],gate,flush=True)
    admissions=[]
    for horizon in [1,24]:
        own=[r for r in receipts if r['horizon_hours']==horizon and r['factor']=='pca_ou_reversal']
        beats=all(r['independent']['raw_ic_mean']>next(x['independent']['raw_ic_mean']for x in receipts
            if x['horizon_hours']==horizon and x['factor']==control and x['split']==r['split'])
            for r in own for control in ['reversal1','reversal239'])
        passes=all(r['positive_gate']for r in own)and beats and not any(r['duplicate_ge99']for r in dedup)
        admissions.append(dict(horizon_hours=horizon,all_positive_gates=all(r['positive_gate']for r in own),
            beats_both_reversal_controls_all_splits=beats,scoped_dedup_pass=not any(r['duplicate_ge99']for r in dedup),
            predictive_stage_admitted=passes,financial_stage_admitted=False))
    for p,digest in read(HERE/'predictive_implementation_freeze.json').items():assert sha(p)==digest
    result=dict(actual_factor_analysis_calls=len(receipts),isolated_registry_views=4,global_registry_modified=False,
        unique_1h_labels=pre['labels']['1']['observations'],unique_24h_labels=pre['labels']['24']['observations'],
        labels_overlap_across_horizons=True,reused_full_and_year_splits=True,
        receipts=receipts,admissions=admissions,scoped_deduplication=dedup,
        reconstructed_ic_rows=sum(r['independent']['independent_ic_rows']for r in receipts),
        reconstructed_group_cells=sum(r['independent']['independent_group_cells']for r in receipts),
        group_nav_not_funded_returns=True,all_views_same_ou_admissibility_mask=True,
        financial_account_returns=None,funded_accounts=0,combination_accounts=0,new_accepted_independent_alphas=0,
        independent_oos=False,source_account_replication=False,orders_enabled=False,services_changed=False)
    save(HERE/'predictive_results.json',result)
    print('ACTUAL FA',len(receipts),'ADMISSION',admissions,flush=True)


if __name__=='__main__':main()
