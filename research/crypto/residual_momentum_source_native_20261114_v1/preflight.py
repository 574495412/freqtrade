"""Actual StrategyResolver/DataProvider features and independent math custody."""
import ast
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import boot

native=boot()
from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.residual_momentum import source_residual_momentum
from freqtrade.enums import CandleType, RunMode
from freqtrade.resolvers import StrategyResolver

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')

def source_math(close):
    note=json.loads((HERE/'source/04_out_of_sample.ipynb').read_text())
    env={'pd':pd,'np':np,'return_panel_full':close.pct_change(fill_method=None),
         'FORMATION_H':336,'display':lambda *args:None}
    with contextlib.redirect_stdout(io.StringIO()):
        for i in [4,6,7,8]:
            code=''.join(note['cells'][i]['source'])
            exec(compile(code,f'captured-source-cell-{i}','exec'),env)
    return env

def scalar_score(returns,i,j):
    # Scalar covariance / variance: separate math.fsum implementation, no pandas
    # rolling regression. Recompute all336residuals required at each audit date.
    values=returns.to_numpy();residuals=[]
    for end in range(i-335,i+1):
        rows=values[end-719:end+1]
        market=[math.fsum(row)/8 for row in rows]
        asset=[float(row[j])for row in rows]
        mx=math.fsum(market)/720;my=math.fsum(asset)/720
        beta=math.fsum((a-my)*(m-mx)for a,m in zip(asset,market,strict=True))/math.fsum((m-mx)**2 for m in market)
        residuals.append(float(values[end,j])-beta*market[-1])
    return math.fsum(residuals)

def main():
    admission=json.loads((HERE/'input_admission.json').read_text());out=Path(admission['datadir']).parent
    assert not (HERE/'native_preflight.json').exists()
    config=json.loads((HERE.parent/'spread_liquidity_native_20261114_v1/EdgeLiquidity_1000.json').read_text())
    config.update(strategy='ResidualInputs',strategy_path=str(HERE),user_data_dir=out,
                  datadir=Path(admission['datadir']),runmode=RunMode.BACKTEST,
                  candle_type_def=CandleType.FUTURES,timeframe='1h',startup_candle_count=1464,
                  timerange='1735689600-1788220799',research_diagnostic_only=True,
                  research_admission=str(HERE/'input_admission.json'))
    config['exchange']['pair_whitelist']=admission['pairs']
    save(HERE/'input_config.json',config)
    owner=StrategyResolver.load_strategy(config);owner.dp=DataProvider(config,None);owner.bot_start()
    close=owner.research_close;parts=owner.research_components
    assert len(close)==16056 and len(close.columns)==8
    original=source_math(close)
    cells=0
    for key,source_key in [('beta','beta_full'),('residual','residual_return_full'),('signal','signal_336_full')]:
        pd.testing.assert_frame_equal(parts[key],original[source_key],check_exact=True)
        cells+=int(parts[key].notna().sum().sum())
    prefixes=[]
    for text in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
        cutoff=pd.Timestamp(text);past=close.index<cutoff
        changed=close.copy();changed.loc[~past]*=2.71828
        short=source_residual_momentum(close.loc[past]);altered=source_residual_momentum(changed)
        for key in ['beta','residual','signal','raw']:
            pd.testing.assert_frame_equal(parts[key].loc[past],short[key],check_exact=True)
            pd.testing.assert_frame_equal(parts[key].loc[past],altered[key].loc[past],check_exact=True)
        prefixes.append({'future_boundary':str(cutoff),'prefix_and_perturbation_passed':True})
    maximum=0.;scalar_cells=0
    for text in ['2025-01-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
        label=pd.Timestamp(text)-pd.Timedelta(hours=1);index=close.index.get_loc(label)
        for j,pair in enumerate(close.columns):
            value=scalar_score(parts['returns'],index,j)
            error=abs(value-float(parts['signal'].at[label,pair]));maximum=max(maximum,error);scalar_cells+=1
    assert maximum<1e-10,maximum
    # Native populate_indicators must map prior completed bar to each decision.
    indicator_cells=0
    for pair in admission['pairs']:
        frame=owner.dp.get_pair_dataframe(pair,'1h','futures')
        observed=owner.populate_indicators(frame.copy(),{'pair':pair})
        for key in ['signal','raw']:
            expected=parts[key][pair].reindex(pd.DatetimeIndex(frame.date)-pd.Timedelta(hours=1)).to_numpy()
            np.testing.assert_array_equal(observed['residual_'+key],expected);indicator_cells+=len(frame)
    days=pd.date_range('2025-01-01','2026-08-31 23:00',tz='UTC',freq='168h')
    eligible=days[days+pd.Timedelta(hours=169)<=pd.Timestamp('2026-08-31 23:00',tz='UTC')]
    evaluation=eligible-pd.Timedelta(hours=1)
    decision=pd.DataFrame(parts['signal'].reindex(evaluation).to_numpy(),index=eligible,columns=close.columns)
    assert np.isfinite(decision).all().all() and len(decision)==86
    close.to_parquet(out/'native_close.parquet')
    for key in ['beta','residual','signal','raw']:parts[key].to_parquet(out/('native_'+key+'.parquet'))
    save(HERE/'implementation_freeze.json',{str(p):sha(p)for p in [ROOT/'freqtrade/data/residual_momentum.py',HERE/'ResidualInputs.py',HERE/'preflight.py',HERE/'protocol.json',HERE/'input_admission.json',HERE/'input_config.json',HERE/'bootstrap.py']})
    save(HERE/'native_preflight.json',{'passed':True,'native_commit':native['commit'],'native_original_files_verified':len(native['files']),'rows':len(close),'assets':8,'source_math_finite_cells_exact':cells,'independent_scalar_score_cells':scalar_cells,'maximum_scalar_score_error':maximum,'prefix_checks':prefixes,'native_indicator_cells':indicator_cells,'complete_nonoverlapping_week_labels':len(decision),'first_decision':str(eligible[0]),'last_complete_decision':str(eligible[-1]),'financial_accounts':0,'source_financial_engine_executed':False,'source_notebooks_executed':False,'source_pure_math_cells_executed':[4,6,7,8]})
    print('Native source/preflight complete;',len(decision),'labels;',cells,'exactsourcecells;',scalar_cells,'scalars;error',maximum)

if __name__=='__main__':main()
