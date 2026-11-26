"""Source predictor parity and native past-only eligibility; no account simulator."""
import ast
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.data.har_volatility import HAR, har_forecasts, known_feature_mask, source_half_weights

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]


def pure_source_functions():
    path=HERE/'source/src/ivr/models.py';tree=ast.parse(path.read_text());wanted=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in ['smear','har_fit_predict','naive_fit_predict']]
    assert len(wanted)==3
    namespace={'np':np};exec(compile(ast.Module(body=wanted,type_ignores=[]),str(path),'exec'),namespace)
    return namespace


def features(symbol):return pd.read_parquet(ROOT/'user_data/crypto_quant'/HERE.name/(symbol+'_daily_features.parquet'))


@pytest.mark.parametrize('symbol',['BTCUSDT','ETHUSDT'])
def test_actual_forecasts_match_pinned_source_evaluation(symbol):
    d=features(symbol);pure=pure_source_functions();spec=importlib.util.spec_from_file_location('source_evaluate',HERE/'source/src/ivr/evaluate.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    y=d.target_lrv.to_numpy();X=d[HAR].to_numpy();valid=known_feature_mask(d)&np.isfinite(y)
    for mode,fn in [('HAR',pure['har_fit_predict']),('Naive',pure['naive_fit_predict'])]:
        source,folds=m.walk_forward(len(d),valid,730,30,lambda train,test:fn(X[train],y[train],X[test]))
        native,receipts=har_forecasts(d,mode)
        np.testing.assert_allclose(native.to_numpy()[valid],source[valid],rtol=1e-12,atol=1e-15,equal_nan=True)
        assert len(folds)==len(receipts)
        for row in receipts:assert pd.Timestamp(row['latest_realised_target_day'])<=pd.Timestamp(row['origin'])
        assert np.isfinite(native.iloc[-1])and np.isnan(source[-1])


@pytest.mark.parametrize('symbol',['BTCUSDT','ETHUSDT'])
def test_actual_prefix_and_future_target_shocks(symbol):
    d=features(symbol)
    for mode in ['HAR','Naive']:
        full,_=har_forecasts(d,mode)
        for cutoff in ['2024-12-31','2025-07-31','2026-01-31']:
            prefix=d.loc[:cutoff].copy()
            prefix['target_lrv']=prefix.lrv.shift(-1);prefix['target_rv']=prefix.rv.shift(-1);prefix['target_ret']=prefix.ret.shift(-1)
            current,_=har_forecasts(prefix,mode)
            pd.testing.assert_series_equal(current,full.loc[:cutoff],atol=1e-15,rtol=1e-12)
            assert np.isfinite(current.iloc[-1])
            changed=d.copy();future=changed.index>pd.Timestamp(cutoff)
            for col in changed:
                if not col.startswith('target_'):changed.loc[future,col]*=3
            changed['target_lrv']=changed.lrv.shift(-1)
            shock,_=har_forecasts(changed,mode)
            pd.testing.assert_series_equal(current,shock.loc[:cutoff],atol=1e-15,rtol=1e-12)


def test_source_sizing_parity():
    path=HERE/'source/src/ivr/sizing.py';spec=importlib.util.spec_from_file_location('source_pure_weights',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    def forbidden(*args,**kw):raise AssertionError('Source account simulator forbidden')
    m.backtest=m.perf=forbidden
    d=features('BTCUSDT');weight,_,forecast=source_half_weights(d)
    np.testing.assert_allclose(weight.to_numpy(),.5*m.target_weights(forecast.to_numpy()),rtol=1e-12,atol=1e-15,equal_nan=True)
    assert weight.index[0]==d.index[0].tz_localize('UTC')+pd.Timedelta(days=1)


def test_insufficient_history_and_incorrect_training_label():
    d=features('BTCUSDT');short,_=har_forecasts(d.iloc[:730]);assert short.isna().all()
    bad=d.copy();bad.iloc[500,bad.columns.get_loc('target_lrv')]+=1
    with pytest.raises(ValueError,match='training labels'):har_forecasts(bad)
