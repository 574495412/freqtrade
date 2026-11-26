"""Forecast scoring after outcomes mature; no return, wallet or candidate calculator."""
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent


def forecasts(path,pair):
    with gzip.open(path,'rt')as f:trace=json.load(f)
    d=pd.DataFrame(trace['forecasts'][pair]).set_index('origin');d.index=pd.to_datetime(d.index);return d.variance


def main():
    plan=json.loads((HERE/'plan.json').read_text());rows=[]
    for pair,path in plan['candidates'][0]['daily_feature_paths'].items()if 'daily_feature_paths'in plan['candidates'][0]else json.loads(Path(plan['candidates'][0]['config']['path']).read_text())['daily_feature_paths'].items():
        d=pd.read_parquet(path);target_days=pd.date_range('2025-01-01','2026-08-30',freq='D');origins=target_days-pd.Timedelta(days=1)
        true=d.rv.reindex(target_days).to_numpy();assert np.isfinite(true).all()and(true>0).all()
        predictions={}
        for mode,cost in [('har',20),('naive',20)]:
            root=Path(plan['output_root'])/(mode+'_1000_conservative_'+str(cost)+'bps')
            fc=forecasts(root/'strategy_trace.json.gz',pair).reindex(origins).to_numpy();assert np.isfinite(fc).all()and(fc>0).all();predictions[mode]=fc
        for mode,fc in predictions.items():
            ratio=true/fc;qlike=float(np.mean(ratio-np.log(ratio)-1));mse=float(np.mean((true-fc)**2))
            rows.append(dict(pair=pair,model=mode,target_days=len(target_days),first_target_day=str(target_days[0]),last_target_day=str(target_days[-1]),qlike=qlike,mse_variance=mse,diagnostic_only_not_profit=True))
    result=dict(passed=True,rows=rows,final_partial_day_excluded=True,future_outcome_mask_not_used_for_native_forecast=True,independent_oos=False,new_factor_ic_claim=False)
    (HERE/'forecast_scores.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
