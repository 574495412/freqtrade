"""Native Registry/FA on causal observed flow; not funded account returns."""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,'/Users/chrise/GitHub/Vibe-Trading/agent')
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ranks(values):
    order=sorted(range(len(values)),key=values.__getitem__);result=[0.]*len(values);i=0
    while i<len(values):
        end=i+1
        while end<len(values) and values[order[end]]==values[order[i]]:end+=1
        for j in range(i,end):result[order[j]]=(i+1+end)/2
        i=end
    return result


def scalar_ic(a,b):
    a=ranks(a);b=ranks(b);ac=math.fsum(a)/len(a);bc=math.fsum(b)/len(b)
    return math.fsum((x-ac)*(y-bc) for x,y in zip(a,b,strict=True))/math.sqrt(math.fsum((x-ac)**2 for x in a)*math.fsum((y-bc)**2 for y in b))


def main():
    protocol=json.loads((HERE/'protocol.json').read_text());admission=json.loads((HERE/'input_admission_v2.json').read_text())
    if not json.loads((HERE/'native_preflight.json').read_text())['passed']:raise ValueError('Native preflight required')
    out=Path(admission['flow_input']['path']).parent
    directory=HERE/'predictive';directory.mkdir(exist_ok=False)
    native=pd.read_parquet(out/'native_features.parquet')
    dates=pd.date_range('2025-01-01T00:00Z','2026-08-31T16:00Z',freq='4h')
    labels=pd.DataFrame(index=dates,columns=protocol['pairs'],dtype=float);close=labels.copy()
    for pair in labels.columns:
        sym=pair.split('/')[0]
        prices=pd.read_feather(Path(admission['native_datadir'])/f'futures/{sym}_USDT_USDT-1h-futures.feather').set_index('date')
        labels[pair]=prices.open.reindex(dates+pd.Timedelta(hours=5)).to_numpy()/prices.open.reindex(dates+pd.Timedelta(hours=1)).to_numpy()-1
        close[pair]=prices.close.reindex(dates-pd.Timedelta(hours=1)).to_numpy()
    if not np.isfinite(labels).all().all():raise ValueError('Native price horizon not complete')
    raw={field:native.pivot(index='decision_date',columns='pair',values=field).reindex(index=dates,columns=labels.columns) for field in protocol['features']}
    zoo=directory/'zoo/core';zoo.mkdir(parents=True)
    for field in protocol['features']:
        meta=dict(id='core_flow_'+field,nickname='Native flow '+field,theme=['microstructure'],
            formula_latex=field,columns_required=['close'],extras_required=['native_'+field],universe=['crypto'],frequency=['1h'],
            decay_horizon=4,min_warmup_bars=0,stage='research',
            notes='Isolated local diagnostic view of exact author kernel computed inside native IStrategy.20completed1h bars,decision then1h native execution delay. A local direction hypothesis,not author strategy,accepted alpha or funded account.')
        (zoo/('flow_'+field+'.py')).write_text('__alpha_meta__ = '+repr(meta)+'\n\ndef compute(panel):\n    return panel['+repr('native_'+field)+'].copy()\n')
    registry=Registry(zoo_root=directory/'zoo');panel={'close':close,**{'native_'+k:v for k,v in raw.items()}}
    from src.factors import registry as owner_registry
    from src.factors import factor_analysis_core as owner_math
    from src.tools import factor_analysis_tool as owner_tool
    owners=[Path(owner_registry.__file__),Path(owner_math.__file__),Path(owner_tool.__file__),Path(__file__),*zoo.glob('*.py')]
    (directory/'owners.json').write_text(json.dumps({str(p):sha(p) for p in owners},indent=2)+'\n')
    receipts=[];ic_rows=0;group_cells=0;maxerror=0.
    for field,original in raw.items():
        factor=registry.compute('core_flow_'+field,panel)
        pd.testing.assert_frame_equal(factor,original,check_exact=True)
        for name,subset in [('all',dates),('calendar2025',dates[dates.year==2025]),('calendar2026_through_aug31',dates[dates.year==2026])]:
            folder=directory/field/name;folder.mkdir(parents=True)
            factor.loc[subset].to_csv(folder/'factor.csv');labels.loc[subset].to_csv(folder/'returns.csv')
            value=json.loads(FactorAnalysisTool().execute(factor_csv=str(folder/'factor.csv'),return_csv=str(folder/'returns.csv'),output_dir=str(folder/'native'),n_groups=3))
            if value['status']!='ok':raise ValueError(value)
            (folder/'native_result.json').write_text(json.dumps(value,indent=2)+'\n')
            # Validate the actual owner-parsed CSV inputs; arithmetic is independent.
            f=pd.read_csv(folder/'factor.csv',index_col=0,parse_dates=True);y=pd.read_csv(folder/'returns.csv',index_col=0,parse_dates=True)
            ic=pd.read_csv(folder/'native/ic_series.csv',index_col=0).iloc[:,0]
            group=pd.read_csv(folder/'native/group_equity.csv',index_col=0).to_numpy();running=[1.,1.,1.];expected=[];gindex=0
            for day,scores in f.iterrows():
                valid=scores.notna() & y.loc[day].notna();a=scores.loc[valid].tolist();b=y.loc[day,valid].tolist()
                if len(a)<5:continue
                if len(set(a))<2:continue
                expected.append(scalar_ic(a,b));order=sorted(range(len(a)),key=lambda j:(a[j],j));n=len(a)
                cuts=[(n-1)*((g/3*100)/100) for g in [1,2]]
                members=[[j for k,j in enumerate(order) if (0 if k<=cuts[0] else 1 if k<=cuts[1] else 2)==g] for g in range(3)]
                for g in range(3):running[g]*=1+math.fsum(b[j] for j in members[g])/len(members[g])
                error=max(abs(a-b) for a,b in zip(running,group[gindex],strict=True));maxerror=max(maxerror,error);gindex+=1
            if len(expected)!=len(ic) or np.max(np.abs(ic.to_numpy()-expected))>1e-12 or maxerror>1e-9:raise ValueError('Independent IC/group audit')
            receipts.append(dict(factor=field,split=name,result=value,positive_gate=value['ic_mean']>.03 and value['ir']>.5 and value['ic_positive_ratio']>.55))
            ic_rows+=len(ic);group_cells+=group.size
            print('FA',field,name,value['ic_mean'],value['ir'],flush=True)
    comparisons=[]
    for i,left in enumerate(raw):
        for right in list(raw)[i+1:]:
            correlation=raw[left].corrwith(raw[right],axis=1)
            comparisons.append(dict(left=left,right=right,dates=int(correlation.notna().sum()),mean_abs_daily_pearson=float(correlation.abs().mean()),duplicate=bool(correlation.abs().mean()>.99)))
    old=pd.read_parquet(ROOT/'user_data/crypto_quant/funding_cross_section_native_20261114_v1/native_factor.parquet')
    for field,factor in raw.items():
        common=factor.index.intersection(old.index);a=factor.reindex(common);b=old.reindex(index=common,columns=a.columns)
        correlation=a.corrwith(b,axis=1)
        comparisons.append(dict(left=field,right='prior_funding7',dates=int(correlation.notna().sum()),mean_abs_daily_pearson=float(correlation.abs().mean()),duplicate=bool(correlation.abs().mean()>.99)))
    result=dict(actual_registry_views=3,global_registry_modified=False,actual_factor_analysis_calls=len(receipts),
        unique_four_hour_labels=len(dates),assets=29,independent_ic_rows=ic_rows,independent_group_nav_cells=group_cells,
        maximum_group_nav_error=maxerror,scoped_dedup=comparisons,all_registry_dedup_claim=False,
        unknown_factor_cells={k:int(v.isna().sum().sum()) for k,v in raw.items()},
        native_funded_accounts=0,net_return_evidence=None,independent_oos_certified=False,source_strategy_reproduced=False,
        protocol_sha256=sha(HERE/'protocol.json'),owners_sha256=sha(directory/'owners.json'),receipts=receipts)
    (HERE/'predictive_diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
    print('NATIVE FA COMPLETE',len(receipts),'calls;',ic_rows,'IC rows;',group_cells,'group NAV cells',flush=True)


if __name__=='__main__':main()
