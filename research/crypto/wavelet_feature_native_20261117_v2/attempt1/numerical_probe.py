"""Real source gain/normalization/DWT/CPU-model chronology, without a wallet."""

# ruff: noqa: S101
# Explicit numerical-admission assertions, with no financial account execution.
import hashlib
import json
import os
import sys
import threading
import time
import warnings
from pathlib import Path

from research.crypto.wavelet_feature_native_20261117_v2.source_reference import (
    HERE,
    bootstrap,
    build_reference,
)


if not __debug__:
    raise RuntimeError('Numerical admission requires assertions')


def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def matrix_sha(np,values):
    a=np.ascontiguousarray(values,dtype='<f8')
    return hashlib.sha256(a.tobytes()).hexdigest()


def run_case(prices,pair,name):
    owner=build_reference()
    libs=bootstrap()
    np,pd=libs['np'],libs['pd']
    from sklearn.multioutput import MultiOutputRegressor
    events=[]
    class ObservedMultiOutput(MultiOutputRegressor):
        def fit(self,x,y,**kwargs):
            result=super().fit(x,y,**kwargs)
            events.append(dict(kind='fit',rows=len(x),columns=x.shape[1],
                               x_sha256=matrix_sha(np,x),y_sha256=matrix_sha(np,y),
                               latest_label_row=len(x)+owner.lookahead-1,
                               model_types=sorted({type(m).__name__ for m in self.estimators_}),
                               first_booster_parameters=self.estimators_[0].booster_.params))
            return result

        def predict(self,x,**kwargs):
            result=super().predict(x,**kwargs)
            assert np.isfinite(result).all()
            events.append(dict(kind='predict',rows=len(x),columns=x.shape[1],
                               x_sha256=matrix_sha(np,x),output_sha256=matrix_sha(np,result)))
            return result
    # Read-only receipt wrapper delegates unchanged matrices to actual sklearn/LightGBM.
    owner.add_rolling_predictions.__func__.__globals__['MultiOutputRegressor']=ObservedMultiOutput
    frame=prices.copy()
    frame['gain']=(100*(frame.close-frame.close.shift(owner.lookahead))/frame.close.shift(owner.lookahead)).fillna(0).round(4)
    frame['predicted_gain']=0.0  # Original populate_indicators precondition.
    normalized=owner.convert_dataframe(frame)
    expected_gain=owner.dataframeUtils.rolling_robust_scale(frame[['gain']],window=128).gain
    np.testing.assert_array_equal(normalized.gain.to_numpy(),expected_gain.to_numpy())
    start=time.monotonic()
    with warnings.catch_warnings(record=True)as caught:
        warnings.simplefilter('default')
        result=owner.add_predictions(frame)
    elapsed=time.monotonic()-start
    fits=[e for e in events if e['kind']=='fit']
    predictions=[e for e in events if e['kind']=='predict']
    assert len(fits)==len(predictions)==len(range(1000,len(frame),2500))
    assert np.isfinite(result.predicted_gain).all()
    assert result.predicted_gain.iloc[:1000].eq(0).all()
    assert result.predicted_gain.iloc[1000:].abs().sum()>0
    assert owner.scale_results is False and owner.strategy_config.scale_results is True
    for row,(fit,prediction)in zip(range(1000,len(frame),2500),zip(fits,predictions,strict=True),strict=True):
        assert fit['latest_label_row']==row-1
        assert prediction['rows']==min(2500,len(frame)-row)
        assert fit['model_types']==['LGBMRegressor']
    # Actual DWT inverse roundtrip on a historical normalized window.
    window=normalized.gain.iloc[1000:1064].to_numpy()
    coefficients=owner.wavelet.get_coeffs(window)
    reconstructed=owner.wavelet.get_values(coefficients)
    roundtrip_error=float(np.max(np.abs(reconstructed[:len(window)]-window)))
    assert roundtrip_error<1e-10
    out=HERE/'numerical_outputs';out.mkdir(exist_ok=True)
    target=out/(pair+'_'+name+'.parquet')
    table=pd.DataFrame(dict(date=prices.index,source_gain=frame.gain,
                            normalized_gain=normalized.gain,raw_source_prediction=result.predicted_gain))
    table.to_parquet(target,index=False)
    receipt=dict(pair=pair,case=name,rows=len(frame),first=str(prices.index[0]),last=str(prices.index[-1]),
                 elapsed_seconds=elapsed,source_scale_results=False,strategy_config_scale_results=True,
                 actual_wavelet=owner.wavelet.wavelet.name,actual_mode=owner.wavelet.mode,
                 coefficient_columns=owner.coeff_table.shape[1],wavelet_roundtrip_max_error=roundtrip_error,
                 events=events,warnings=sorted({str(w.message)for w in caught}),
                 output=dict(path=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest()),
                 no_dummy_features_wavelets_scalers_or_numeric_estimators=True,
                 full_framework_native_constructor_or_pnl_executed=False)
    save(out/(pair+'_'+name+'.json'),receipt)
    print(json.dumps({k:receipt[k]for k in ['pair','case','rows','elapsed_seconds','coefficient_columns']}),flush=True)
    return table,receipt


def main():
    import psutil
    import shutil
    if shutil.disk_usage(HERE).free<1024**3:
        raise RuntimeError('Preserve1GiB free disk guard')
    complete=threading.Event()
    began=time.monotonic()
    def monitor():
        process=psutil.Process()
        while not complete.wait(2):
            status=dict(pid=process.pid,elapsed_seconds=time.monotonic()-began,rss_bytes=process.memory_info().rss)
            save(HERE/'numerical_progress.json',status)
            if status['rss_bytes']>768*1024**2 or status['elapsed_seconds']>600:
                save(HERE/'numerical_resource_guard.json',status)
                os._exit(73)
    threading.Thread(target=monitor,daemon=True).start()
    try:
        libs=bootstrap();np,pd=libs['np'],libs['pd']
        checks=[]
        for pair in ['BTC','ETH']:
            prices=pd.read_parquet(HERE/(pair+'_15m.parquet')).iloc[:6000]
            full,full_receipt=run_case(prices,pair,'full6000')
            prefix,prefix_receipt=run_case(prices.iloc[:5800],pair,'prefix5800')
            shocked=prices.copy()
            shocked.iloc[5800:,shocked.columns.get_loc('close')]*=np.linspace(2,10,200)
            changed,changed_receipt=run_case(shocked,pair,'future_shock')
            fields=['source_gain','normalized_gain','raw_source_prediction']
            for field in fields:
                np.testing.assert_array_equal(full[field].iloc[:5800],prefix[field])
                np.testing.assert_array_equal(full[field].iloc[:5800],changed[field].iloc[:5800])
            fit=lambda receipt:[(r['x_sha256'],r['y_sha256'])for r in receipt['events']if r['kind']=='fit']
            assert fit(full_receipt)==fit(prefix_receipt)==fit(changed_receipt)
            checks.append(dict(pair=pair,exact_prefix_and_future_shock_equal=True,
                               exact_training_matrices_equal=True,real_model_fit_calls=6,
                               full_prediction_rows=5000,comparison_fields=fields))
        save(HERE/'numerical_admission.json',dict(passed=True,checks=checks,
                                                native_financial_accounts=0,profit_proven=False,
                                                full_signal_risk_and_wallet_integration_complete=False))
    finally:
        complete.set()


if __name__=='__main__':
    try:
        main()
    except BaseException as error:
        save(HERE/'numerical_failure.json',dict(type=type(error).__name__,message=str(error)))
        raise
