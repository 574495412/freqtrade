"""Final export/domain guards preserve actual native probabilities and states."""
import json
from pathlib import Path

import pandas as pd
from bootstrap import boot
from prepare import HERE,OUT,native_component,save


def main():
    boot();start=pd.Timestamp('2025-01-01T00:00Z');cutoff=start+pd.Timedelta(days=3);records=[]
    for name in ['RollingRF','RollingPrior']:
        base,record=native_component(name,start,'final_original')
        old=OUT/'components'/f'{name}_20250101_original/freqai_audit'
        current=OUT/'components'/f'{name}_20250101_final_original/freqai_audit'
        old_predict=pd.read_parquet(next(old.glob('predict_*.parquet')))
        new_predict=pd.read_parquet(next(current.glob('predict_*.parquet')))
        pd.testing.assert_frame_equal(old_predict,new_predict,check_exact=True)
        def changed(frame):
            f=frame.copy();f.loc[f.date.ge(cutoff),['open','high','low','close','volume']]*=11;return f
        for variant,change in [('final_future',changed),('final_truncated',lambda f:f.loc[f.date.lt(cutoff)].copy())]:
            actual,_=native_component(name,start,variant,change)
            columns=['date','&-direction','up','down','do_predict','target_weight','source_signal']
            pd.testing.assert_frame_equal(base.loc[base.date.lt(cutoff),columns].reset_index(drop=True),actual.loc[actual.date.lt(cutoff),columns].reset_index(drop=True),check_exact=True)
        records.append(dict(strategy=name,actual_native_fits=3,old_native_prediction_matrix_exact=True,
                            prefix_future_and_truncation_exact=True,native_model=record))
    save(HERE/'final_revalidation.json',dict(passed=True,records=records,additional_native_component_fits=6,
         change='Pre-window label placeholders restored to unknown for lossless export; probability domain guard rejects values outside[0,1]. No prediction values, model parameters or signals changed.',financial_accounts=0))
    print('Final guard revalidation passed:6 actual native fits, unchanged prediction matrices')


if __name__=='__main__':main()
