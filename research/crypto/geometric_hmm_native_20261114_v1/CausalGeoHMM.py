"""Local native signal adapter around pinned source statistical functions.

Only observations, model fitting, filtering and target exposures are produced.
No source portfolio/backtest or external return simulator is invoked.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m);return m

def source_modules(study):
    sys.path.insert(0,str(study/'dependencies'))
    sys.path.insert(0,str(study.parent/'freqai_rf_native_20261114_v1/dependencies'))
    return [load('geo_author_'+n,study/'source/src'/(n+'.py'))for n in ['geometric','markov','ensemble']]

def calculate(close,study,model_dir):
    """Same504/252bar source fold grid; all observed days are calendar days."""
    if close.isna().any()or not np.isfinite(close).all()or close.le(0).any():raise ValueError('Observed positive daily native closes required')
    if not close.index.equals(pd.date_range(close.index[0],close.index[-1],freq='D')):raise ValueError('Complete daily calendar required')
    geo,markov,ensemble=source_modules(study)
    from threadpoolctl import threadpool_limits
    returns=np.log(close/close.shift(1)).dropna()
    rows=[];models=[];model_dir.mkdir(parents=True,exist_ok=True)
    for fold,train_end in enumerate(range(504,len(returns),252)):
        test_end=min(train_end+252,len(returns));train=returns.iloc[:train_end];test=returns.iloc[train_end:test_end]
        # Unlike the source financial summary's <5-day reporting guard, a
        # streaming predictor must also expose1--4available days. Main full
        # sample folds all exceed5days; source full-sample predictions agree.
        if test.empty:continue
        mom_threshold,rev_threshold=geo.compute_thresholds(train)
        geometric=geo.geometric_signal(test,mom_thresh=mom_threshold,rev_thresh=rev_threshold)
        features=markov._build_features(train).dropna();mu=features.mean();sigma=features.std().replace(0,1)
        x=markov._scale(features,mu,sigma)
        record={'fold':fold,'train_observations':len(train),'train_clean_rows':len(features),
                'train_start':str(train.index[0]),'train_end':str(train.index[-1]),
                'test_start':str(test.index[0]),'test_end':str(test.index[-1]),
                'geo_momentum_threshold':float(mom_threshold),'geo_reversion_threshold':float(rev_threshold),
                'train_X_sha256':hashlib.sha256(x.tobytes()).hexdigest(),'fallback':False}
        try:
            with threadpool_limits(limits=1):model=markov._fit_hmm(x)
            mom_idx,crisis_idx,_=markov._label_states(model)
            test_features=markov._build_features(test,warmup=train.iloc[-250:]).dropna()
            xt=markov._scale(test_features,mu,sigma)
            probabilities=markov._forward_filter(model,xt)
            mom=pd.Series(probabilities[:,mom_idx],index=test_features.index).reindex(test.index)
            crisis=pd.Series(probabilities[:,crisis_idx],index=test_features.index).reindex(test.index)
            path=model_dir/f'fold_{fold}.npz'
            np.savez(path,startprob=model.startprob_,transmat=model.transmat_,means=model.means_,
                     variances=np.diagonal(model.covars_,axis1=1,axis2=2),scaler_mu=mu.to_numpy(),
                     scaler_sigma=sigma.to_numpy(),X_train=x,X_test=xt,probabilities=probabilities,
                     mom_idx=mom_idx,crisis_idx=crisis_idx)
            record.update(model_path=str(path),model_sha256=sha(path),training_log_likelihood=float(model.score(x)),
                          best_seed=int(model.random_state),converged=bool(model.monitor_.converged),
                          momentum_state=mom_idx,crisis_state=crisis_idx)
        except Exception as exc:
            # The source expanding wrapper explicitly falls back to geo/2.
            # Preserve that branch and report it; never pretend a failed HMMfit
            # is a successful learned state model.
            record.update(fallback=True,error=repr(exc));mom=geometric*.5;crisis=pd.Series(0.,index=test.index)
        score=ensemble.ensemble_score(geometric,mom,crisis)
        adjusted=mom.copy();adjusted.loc[crisis>.5]=0
        frame=pd.DataFrame({'geometric':geometric,'momentum_probability':mom,'crisis_probability':crisis,
                            'score':score,'hmm_score':adjusted,'fold':fold},index=test.index)
        for key,value in [('source',score),('geometric',geometric),('markov',adjusted)]:
            # Source run_backtest shifts each fold independently: first test
            # day is cash, even if the preceding fold ended invested.
            labels=ensemble.regime_labels(value)
            position=labels.map({'momentum':1.,'mixed':.5,'reversion':0.})
            frame['target_'+key]=position.shift(1).fillna(0)
        frame['target_always']=1.
        frame['fold_reset']=False;frame.iloc[0,frame.columns.get_loc('fold_reset')]=True
        frame['source_signal']=frame.score.shift(1).fillna(0)
        frame['source_volatility']=returns.rolling(20,min_periods=10).std().mul(np.sqrt(252)).shift(1).reindex(frame.index)
        rows.append(frame);models.append(record)
    result=pd.concat(rows).sort_index()
    if result.index.has_duplicates or not np.isfinite(result.to_numpy(dtype=float)).all():raise ValueError('Invalid complete model input/target grid')
    return result,models

def native_signals(close,study,output):
    capsule=output/'model_capsule.json';features=output/'native_model_features.parquet'
    owners=[study/'source/src'/name for name in ['geometric.py','markov.py','ensemble.py']]
    key={'close_values_sha256':hashlib.sha256(close.to_numpy().tobytes()).hexdigest(),
         'close_index_sha256':hashlib.sha256(close.index.asi8.tobytes()).hexdigest(),
         'adapter_sha256':sha(Path(__file__)),'source_owners':{str(p):sha(p)for p in owners}}
    if capsule.exists():
        record=json.loads(capsule.read_text())
        if record['input_identity']!=key or sha(features)!=record['features_sha256']:raise ValueError('Frozen native model cache drift')
        for m in record['models']:
            if 'model_path'in m and sha(m['model_path'])!=m['model_sha256']:raise ValueError('Saved model drift')
        return pd.read_parquet(features),record['models']
    result,models=calculate(close,study,output/'models')
    result.to_parquet(features)
    capsule.write_text(json.dumps({'input_identity':key,'features_sha256':sha(features),'models':models,
                     'source_financial_engine_executed':False},indent=2)+'\n')
    return result,models
