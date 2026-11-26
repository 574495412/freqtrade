"""Scoped comparison with preserved A10 raw flow scores; no old account retest."""
import hashlib
import json
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def anomaly(frame):
    recent=frame[['buy_quote','quote']].rolling(3,min_periods=3).sum()
    prior=frame[['buy_quote','quote']].shift(3).rolling(30,min_periods=30).sum()
    return recent.buy_quote/recent.quote-prior.buy_quote/prior.quote


def main():
    protocol=json.loads((HERE/'protocol.json').read_text());admission=json.loads((HERE/'input_admission_v2.json').read_text())
    out=Path(admission['flow_input']['path']).parent;native=pd.read_parquet(out/'native_features.parquet')
    old=ROOT/'user_data/crypto_quant/spot_perp_flow_gap_20261107_v001'
    symbols=['ADA','AVAX','BNB','DOGE','DOT','ETH','LINK','LTC','SOL','XRP']
    variants={k:{} for k in ['Gap','SpotOnly','PerpOnly']};inputs={}
    for sym in symbols:
        values={}
        for market in ['spot','perp']:
            path=old/f'{sym}USDT-{market}.parquet';inputs[str(path)]=sha(path);values[market]=anomaly(pd.read_parquet(path))
        pair=sym+'/USDT:USDT'
        for name,score in [('Gap',values['spot']-values['perp']),('SpotOnly',values['spot']),('PerpOnly',values['perp'])]:
            score.index=score.index+pd.Timedelta(days=1);variants[name][pair]=score
    records=[]
    for field in protocol['features']:
        current=native.pivot(index='decision_date',columns='pair',values=field)
        grid=current.index
        mask=(grid>=pd.Timestamp('2025-01-01T00:00Z')) & (grid<pd.Timestamp('2026-09-01T00:00Z')) & (grid.hour==8) & grid.dayofweek.isin([0,3])
        times=grid[mask]
        for name,values in variants.items():
            reference=pd.DataFrame(values).reindex(times.normalize());reference.index=times
            selected=current.reindex(index=times,columns=reference.columns)
            correlation=selected.corrwith(reference,axis=1)
            records.append({'new_field':field,'old_case':name,'old_fixed_assets':10,'shared_scheduled_dates':int(correlation.notna().sum()),
                'mean_abs_cross_sectional_pearson':float(correlation.abs().mean()),'duplicate_gt_99':bool(correlation.abs().mean()>.99)})
    (HERE/'old_flow_dedup.json').write_text(json.dumps({'source_inputs':inputs,'records':records,'old_A10_failures_preserved':True,
        'prior_account_results_recomputed':False,'all_registry_dedup_claim':False,'performed_after_new_factor_diagnostics':True},indent=2)+'\n')
    print('Scoped old-flow dedup',len(records),'comparisons; max',max(r['mean_abs_cross_sectional_pearson'] for r in records))


if __name__=='__main__':main()
