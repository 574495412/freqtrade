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
    frames={name:pd.read_parquet(out/f'native_{name}.parquet')for name in ['max28','mom21_skip7','lowvol28']}
    labels=pd.read_parquet(out/'native_weekly_labels.parquet');days=labels.index
    close=pd.read_parquet(out/'native_daily_close.parquet')
    price=pd.DataFrame(close.reindex(days-pd.Timedelta(days=1)).to_numpy(),index=days,columns=close.columns)
    assert labels.shape==(86,40) and all(f.index.equals(days) and f.columns.equals(labels.columns)for f in frames.values())
    zoo=folder/'zoo/core';zoo.mkdir(parents=True)
    for name in frames:
        meta=dict(id='core_native_'+name+'_view',nickname='Native MAX source '+name+' diagnostic',
            theme=['volatility'if name.startswith('lowvol')else'momentum'],formula_latex=name,
            columns_required=['close'],extras_required=['native_'+name],universe=['crypto'],
            frequency=['1h'],decay_horizon=7,min_warmup_bars=0,stage='research',
            notes='Isolated native Registry view of actual Freqtrade IStrategy/DataProvider values. MAX28positive/source footnote9; local perpetual/simple-return adaptation. Not upstream Freqtrade factor library or new validated alpha.')
        (zoo/(name+'_view.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+name)+'].copy()\n')
    registry=Registry(zoo_root=folder/'zoo');assert len(registry.list())==3
    owners=[VIBE/'src/factors/registry.py',VIBE/'src/factors/factor_analysis_core.py',
        VIBE/'src/tools/factor_analysis_tool.py',HERE/'predictive.py',*zoo.glob('*.py')]
    save(folder/'owners.json',{str(p):sha(p)for p in owners})
    peers={name:f for name,f in frames.items()if name!='max28'}
    peers.update({name:pd.read_parquet(out/f'native_{name}.parquet')for name in ['raw28','skew28']})
    returns=close.pct_change(fill_method=None)
    # Diagnostics on the same native daily data; not independent factor selection
    # or new economic candidates. The annual hypothesis remains rejected.
    def popskew(x):
        centered=x-x.mean();variance=np.mean(centered**2)
        return np.mean(centered**3)/variance**1.5 if variance else float('nan')
    annual=-returns.rolling(360,min_periods=360).apply(popskew,raw=True)
    peers['annual_low_skew360_recomputed']=pd.DataFrame(annual.reindex(days-pd.Timedelta(days=1)).to_numpy(),index=days,columns=close.columns)
    annual_lowvol=-returns.rolling(63,min_periods=63).std(ddof=1)
    peers['lowvol63_recomputed']=pd.DataFrame(annual_lowvol.reindex(days-pd.Timedelta(days=1)).to_numpy(),index=days,columns=close.columns)
    residual_preflight=read(HERE.parent/'residual_momentum_source_native_20261114_v1/native_preflight.json')
    residual_admission=read(HERE.parent/'residual_momentum_source_native_20261114_v1/input_admission.json')
    residual_path=Path(residual_admission['datadir']).parent/'native_signal.parquet'
    residual=pd.read_parquet(residual_path)
    peers['previous_eight_residual14']=pd.DataFrame(residual.reindex(days-pd.Timedelta(hours=1)).to_numpy(),index=days,columns=residual.columns)
    assert residual_preflight['passed']
    old_plan=read(HERE.parent/'spread_liquidity_native_20261114_v1/plan.json')
    liquidity_path=Path(old_plan['output_root'])/'EdgeLiquidity_1000_15bps/native_features.parquet'
    liquidity=pd.read_parquet(liquidity_path)
    for name in ['edge30','amihud30','lowvol63']:
        peers['previous_six_'+name]=liquidity.pivot(index='decision_date',columns='pair',values=name).reindex(days)
    dedup=[]
    for name,peer in peers.items():
        common=[p for p in labels.columns if p in peer.columns];values=[]
        for day in days:
            a=frames['max28'].loc[day,common].to_numpy();b=peer.loc[day,common].to_numpy()
            good=np.isfinite(a)&np.isfinite(b)
            if good.sum()>=5:
                value=corr(a[good].tolist(),b[good].tolist())
                if math.isfinite(value):values.append(abs(value))
        mean=math.fsum(values)/len(values)if values else None
        dedup.append(dict(peer=name,shared_assets=len(common),observations=len(values),
            mean_abs_pearson=mean,duplicate_gt_99=mean is not None and mean>.99))
    save(folder/'deduplication.json',dict(scope='10declared peers only; six same40diagnostics, previous8residual and3previous6liquidity; no global novelty claim',
        parent_bindings={str(p):sha(p)for p in [residual_path,liquidity_path]},comparisons=dedup))
    receipts=[];ic_rows=group_cells=0;maximum=0.
    for name,factor in frames.items():
        view=registry.compute('core_native_'+name+'_view',{'close':price,'native_'+name:factor})
        pd.testing.assert_frame_equal(view,factor,check_exact=True)
        for split,subset in [('all',days),('calendar2025',days[days.year==2025]),('calendar2026_through_aug',days[days.year==2026])]:
            target=folder/name/split;target.mkdir(parents=True)
            f,y=view.loc[subset],labels.loc[subset]
            f.to_csv(target/'factor.csv');y.to_csv(target/'returns.csv')
            value=json.loads(FactorAnalysisTool().execute(factor_csv=str(target/'factor.csv'),return_csv=str(target/'returns.csv'),output_dir=str(target/'native'),n_groups=protocol['predictive']['n_groups']))
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
            receipts.append(dict(factor=name,split=split,observations=len(subset),result=value,positive_gate=gate))
            ic_rows+=len(observed);group_cells+=group.size
            save(HERE/'predictive_progress.json',dict(completed_actual_calls=len(receipts),receipts=receipts))
    admitted=all(row['positive_gate']for row in receipts if row['factor']=='max28')and not any(row['duplicate_gt_99']for row in dedup)
    save(HERE/'predictive_results.json',dict(actual_registry_views=3,global_registry_modified=False,
        actual_factor_analysis_calls=len(receipts),unique_nonoverlapping_weeks=86,assets=40,
        independent_ic_rows_including_reused_splits=ic_rows,independent_group_nav_cells=group_cells,
        maximum_group_nav_error=maximum,label=protocol['label'],receipts=receipts,deduplication=dedup,
        source_factor_admitted_to_financial_stage=admitted,financed_account_returns=None,
        historical_funded_accounts=0,combination_accounts=0,accepted_new_alphas=0,
        independent_oos_certified=False,group_curves_are_funded_returns=False))
    print('ACTUAL NATIVE FA',len(receipts),'calls;',ic_rows,'IC rows;',group_cells,'group cells; MAX financial-stage admission',admitted)
    for r in receipts:
        v=r['result'];print(r['factor'],r['split'],r['observations'],v['ic_mean'],v['ir'],v['ic_positive_ratio'],r['positive_gate'])


if __name__ == '__main__':
    main()
