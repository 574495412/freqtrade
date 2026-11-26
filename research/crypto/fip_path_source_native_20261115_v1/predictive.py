"""Owning Registry/factor_analysis calls, with independent ranks and group tie-outs."""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import HERE, ROOT, boot

boot()
VIBE = ROOT.parent/'Vibe-Trading/agent'
sys.path.insert(0,str(VIBE))
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2,default=str,allow_nan=False)+'\n')


def ranks(values):
    ordered=sorted(range(len(values)),key=values.__getitem__)
    result=[0.]*len(values);i=0
    while i<len(values):
        j=i+1
        while j<len(values) and values[ordered[j]]==values[ordered[i]]:j+=1
        for k in range(i,j):result[ordered[k]]=(i+1+j)/2
        i=j
    return result


def corr(left,right,rank=False):
    if rank:left,right=ranks(left),ranks(right)
    a,b=math.fsum(left)/len(left),math.fsum(right)/len(right)
    den=math.sqrt(math.fsum((x-a)**2 for x in left)*math.fsum((y-b)**2 for y in right))
    return math.fsum((x-a)*(y-b)for x,y in zip(left,right,strict=True))/den if den else float('nan')


def main():
    preflight=read(HERE/'native_preflight.json');assert preflight['passed']
    for path,digest in read(HERE/'implementation_freeze.json').items():assert sha(path)==digest,path
    protocol=read(HERE/'protocol.json');out=Path(preflight['factor_output'])
    folder=HERE/'predictive';folder.mkdir(exist_ok=False)
    names=protocol['factor_diagnostics']['scores']+protocol['factor_diagnostics']['controls']
    frames={name:pd.read_parquet(out/f'native_{name}.parquet')for name in names}
    labels=pd.read_parquet(out/'native_weekly_labels.parquet');days=labels.index
    close=pd.read_parquet(out/'native_daily_close.parquet')
    price=pd.DataFrame(close.reindex(days-pd.Timedelta(days=1)).to_numpy(),index=days,columns=close.columns)
    assert labels.shape==(86,40) and all(f.index.equals(days)and f.columns.equals(labels.columns)for f in frames.values())
    zoo=folder/'zoo/core';zoo.mkdir(parents=True)
    for name in frames:
        meta=dict(id='core_native_'+name+'_view',nickname='Native FIP source '+name+' diagnostic',
            theme=['volatility'if name.startswith('lowvol')else'momentum'],formula_latex=name,
            columns_required=['close'],extras_required=['native_'+name],universe=['crypto'],
            frequency=['1h'],decay_horizon=7,min_warmup_bars=0,stage='research',
            notes='Local isolated Registry view of actual native IStrategy/DataProvider. Signed FIP=PRET*source_modified is local directional hypothesis; source original/modified are not unconditional directional alpha. Not source account reproduction or a validated factor.')
        (zoo/(name+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+name)+'].copy()\n')
    registry=Registry(zoo_root=folder/'zoo');assert len(registry.list())==7
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',
        VIBE/'src/tools/factor_analysis_tool.py',HERE/'predictive.py',*zoo.glob('*.py')]
    save(folder/'owners.json',{str(p):sha(p)for p in owners})
    peers=dict(frames)
    for window in [63,252]:
        for component in ['id','source_original','source_modified']:
            name=component+str(window);peers[name]=pd.read_parquet(out/f'native_{name}.parquet')
    maxflight=read(HERE.parent/'max_lottery_source_native_20261115_v1/native_preflight.json')
    oldroot=Path(maxflight['factor_output'])
    pd.testing.assert_frame_equal(labels,pd.read_parquet(oldroot/'native_weekly_labels.parquet'),check_exact=True)
    bindings={}
    for name in ['max28','mom21_skip7','lowvol28','skew28']:
        path=oldroot/f'native_{name}.parquet';peers['previous40_'+name]=pd.read_parquet(path);bindings[str(path)]=sha(path)
    dedup=[]
    for name in protocol['factor_diagnostics']['scores']:
        for other,peer in peers.items():
            if other==name:continue
            values=[]
            assert peer.index.equals(days)and peer.columns.equals(labels.columns)
            for day in days:
                a,b=frames[name].loc[day].to_numpy(),peer.loc[day].to_numpy()
                good=np.isfinite(a)&np.isfinite(b)
                if good.sum()>=5:
                    value=corr(a[good].tolist(),b[good].tolist())
                    if math.isfinite(value):values.append(abs(value))
            mean=math.fsum(values)/len(values)if values else None
            dedup.append(dict(factor=name,peer=other,shared_assets=40,observations=len(values),
                mean_abs_pearson=mean,duplicate_ge_99=mean is not None and mean>=.99))
    save(folder/'deduplication.json',dict(scope='16declared same40 peers per candidate; no allRegistry or independent-alpha claim',
        parent_bindings=bindings,comparisons=dedup))
    receipts=[];ic_rows=group_cells=0;maximum=0.
    for name,factor in frames.items():
        view=registry.compute('core_native_'+name+'_view',{'close':price,'native_'+name:factor})
        pd.testing.assert_frame_equal(view,factor,check_exact=True)
        for split,subset in [('all',days),('calendar2025',days[days.year==2025]),('calendar2026_through_aug',days[days.year==2026])]:
            target=folder/name/split;target.mkdir(parents=True)
            f,y=view.loc[subset],labels.loc[subset]
            f.to_csv(target/'factor.csv');y.to_csv(target/'returns.csv')
            value=json.loads(FactorAnalysisTool().execute(factor_csv=str(target/'factor.csv'),return_csv=str(target/'returns.csv'),output_dir=str(target/'native'),n_groups=protocol['factor_diagnostics']['groups']))
            assert value['status']=='ok',value
            observed=pd.read_csv(target/'native/ic_series.csv',index_col=0).iloc[:,0].to_numpy()
            expected=[corr(a.tolist(),b.tolist(),True)for a,b in zip(f.to_numpy(),y.to_numpy(),strict=True)]
            assert np.max(np.abs(observed-expected))<1e-12
            group=pd.read_csv(target/'native/group_equity.csv',index_col=0).to_numpy();running=[1.]*10
            for i,(scores,forward)in enumerate(zip(f.to_numpy().tolist(),y.to_numpy().tolist(),strict=True)):
                order=sorted(range(40),key=lambda j:(scores[j],j))
                for g in range(10):running[g]*=1+math.fsum(forward[j]for j in order[4*g:4*g+4])/4
                maximum=max(maximum,max(abs(a-b)for a,b in zip(running,group[i],strict=True)))
            assert maximum<1e-10,maximum
            gate=value['ic_mean']>.03 and value['ir']>.5 and value['ic_positive_ratio']>.55
            receipts.append(dict(factor=name,split=split,observations=len(subset),result=value,
                raw_mean_ic=float(observed.mean()),positive_gate=gate))
            ic_rows+=len(observed);group_cells+=group.size
            save(HERE/'predictive_progress.json',dict(completed_actual_calls=len(receipts),receipts=receipts))
    admissions=[]
    for name in protocol['factor_diagnostics']['scores']:
        window=name.removeprefix('signed_path')
        rows=[r for r in receipts if r['factor']==name]
        beat=all(r['raw_mean_ic']>next(v['raw_mean_ic']for v in receipts if v['factor']=='pret'+window and v['split']==r['split'])for r in rows)
        duplicate=any(r['duplicate_ge_99']for r in dedup if r['factor']==name)
        passed=all(r['positive_gate']for r in rows)and beat and not duplicate
        admissions.append(dict(factor=name,all_positive_gates=all(r['positive_gate']for r in rows),
            beats_same_window_pret_all_splits=beat,scoped_duplicate=duplicate,predictive_stage_admitted=passed,
            financial_stage_admitted=False,independent_alpha_proven=False))
    save(HERE/'predictive_results.json',dict(actual_registry_views=7,global_registry_modified=False,
        actual_factor_analysis_calls=len(receipts),unique_nonoverlapping_weeks=86,assets=40,
        independent_ic_rows_including_reused_splits=ic_rows,independent_group_nav_cells=group_cells,
        maximum_group_nav_error=maximum,label=protocol['label'],receipts=receipts,deduplication=dedup,
        admissions=admissions,funding_and_historical_rule_gates_unresolved=True,
        financed_account_returns=None,historical_funded_accounts=0,combination_accounts=0,accepted_new_alphas=0,
        independent_oos_certified=False,group_curves_are_funded_returns=False))
    print('ACTUAL NATIVE FA',len(receipts),'calls;',ic_rows,'IC rows;',group_cells,'group cells;')
    for r in receipts:
        v=r['result'];print(r['factor'],r['split'],r['observations'],v['ic_mean'],v['ir'],v['ic_positive_ratio'],r['positive_gate'])
    print('FROZEN PREDICTIVE ADMISSION',admissions)


if __name__=='__main__':main()
