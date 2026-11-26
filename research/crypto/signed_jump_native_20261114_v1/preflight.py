"""Native StrategyResolver/DataProvider and independent daily moment audit."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import boot

native=boot()
from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.signed_jump import daily_signed_variation
from freqtrade.enums import CandleType,RunMode
from freqtrade.resolvers import StrategyResolver

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')

def scalar_daily(observed):
    rows=[];previous=None;day=None;returns=[]
    def append():
        if day is None:return
        if len(returns)!=288 or any(r is None for r in returns):
            rows.append({'date':day,'known':False});return
        rv=math.fsum(r*r for r in returns);up=math.fsum(r*r for r in returns if r>0);down=math.fsum(r*r for r in returns if r<0)
        if rv==0:rows.append({'date':day,'known':False});return
        rows.append({'date':day,'known':True,'rv':rv,'up':up,'down':down,'sj':up-down,
                     'rsj':(up-down)/rv,'rsk':math.sqrt(288)*math.fsum(r**3 for r in returns)/rv**1.5,
                     'rkt':288*math.fsum(r**4 for r in returns)/rv**2,'day_return':math.fsum(returns)})
    for timestamp,value in zip(observed.date,observed.vwap,strict=True):
        current=timestamp.floor('D')
        if current!=day:append();day=current;returns=[]
        price=None if pd.isna(value)else float(value)
        returns.append(None if price is None or previous is None else math.log(price/previous))
        previous=price
    append()
    return rows

def main():
    assert not (HERE/'native_preflight.json').exists()
    a=json.loads((HERE/'input_admission.json').read_text());out=Path(a['out'])
    c=json.loads((HERE.parent/'residual_momentum_source_native_20261114_v1/input_config.json').read_text())
    c.update(strategy='SignedJumpInputs',strategy_path=str(HERE),user_data_dir=out,datadir=Path(a['native_datadir']),
             runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES,startup_candle_count=10000,
             research_admission=str(HERE/'input_admission.json'),research_diagnostic_only=True)
    c['exchange']['pair_whitelist']=a['pairs'];save(HERE/'input_config.json',c)
    owner=StrategyResolver.load_strategy(c);owner.dp=DataProvider(c,None);owner.bot_start()
    days=pd.date_range('2025-01-01','2026-08-31',tz='UTC',freq='D')
    factors={key:pd.DataFrame(index=days,columns=a['pairs'],dtype=float)for key in ['rsj','rsk','rkt','rv','day_return']}
    maximum={key:0. for key in ['rv','up','down','sj','rsj','rsk','rkt','day_return']}
    scalar_cells=0;unknowns=[];prefix=[];indicators=0
    for pair,daily in owner.research_daily.items():
        observed=owner.research_minute_samples[pair]
        daily.to_parquet(out/'features'/(pair.split('/')[0]+'_native_daily.parquet'))
        for item in scalar_daily(observed):
            row=daily.loc[item['date']]
            if not item['known']:
                assert row[['rsj','rsk','rkt']].isna().all();continue
            for key in maximum:
                error=abs(item[key]-float(row[key]));maximum[key]=max(maximum[key],error);scalar_cells+=1
        for text in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
            cutoff=pd.Timestamp(text);past=observed.date<cutoff
            changed=observed.copy();changed.loc[~past,'quote_volume']*=2.71828
            changed['vwap']=changed.quote_volume/changed.base_volume.replace(0,np.nan)
            short=daily_signed_variation(observed.loc[past]);altered=daily_signed_variation(changed)
            pd.testing.assert_frame_equal(daily.loc[daily.index<cutoff],short,check_exact=True)
            pd.testing.assert_frame_equal(daily.loc[daily.index<cutoff],altered.loc[altered.index<cutoff],check_exact=True)
            prefix.append({'pair':pair,'future_boundary':str(cutoff),'truncation_and_perturbation':True})
        frame=owner.dp.get_pair_dataframe(pair,'1h','futures');mapped=owner.populate_indicators(frame.copy(),{'pair':pair})
        evaluation=pd.DatetimeIndex(frame.date.dt.floor('D'))-pd.Timedelta(days=1)
        for key in factors:
            np.testing.assert_array_equal(mapped['negative_'+key],-daily[key].reindex(evaluation).to_numpy());indicators+=len(frame)
            factors[key][pair]=-daily[key].reindex(days-pd.Timedelta(days=1)).to_numpy()
        unknowns.extend({'pair':pair,'decision':str(day)}for day in days if pd.isna(factors['rsj'].at[day,pair]))
    assert max(maximum.values())<1e-8,maximum
    for key,frame in factors.items():frame.to_parquet(out/'features'/('native_factor_'+key+'.parquet'))
    known=~factors['rsj'].isna().any(axis=1)
    save(HERE/'implementation_freeze.json',{str(p):sha(p)for p in [ROOT/'freqtrade/data/signed_jump.py',HERE/'SignedJumpInputs.py',HERE/'bootstrap.py',HERE/'preflight.py',HERE/'protocol.json',HERE/'input_admission.json',HERE/'input_config.json']})
    save(HERE/'native_preflight.json',{'passed_for_input_diagnostics':True,'native_commit':native['commit'],
         'native_original_files_verified':len(native['files']),'source_formula_pages_visually_inspected':[7,8],
         'native_rows_per_asset':len(observed),'daily_decisions':len(days),'known_joint_decisions':int(known.sum()),
         'unknown_joint_decisions':[str(d)for d in days[~known]],'unknown_asset_decisions':unknowns,
         'independent_scalar_moment_cells':scalar_cells,'maximum_scalar_errors':maximum,
         'actual_prefix_checks':prefix,'native_indicator_cells':indicators,
         'full_financial_input_admitted':bool(known.all()),'financial_accounts':0,'returns':None})
    print('Native preflight;',len(days),'decisions;',known.sum(),'jointknown;',len(unknowns),'assetunknown;',scalar_cells,'scalarcells')

if __name__=='__main__':main()
