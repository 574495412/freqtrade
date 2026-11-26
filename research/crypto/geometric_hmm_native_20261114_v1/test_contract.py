"""Actual saved model and source-boundary checks before any account outcomes."""
import json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from CausalGeoHMM import source_modules,native_signals
from prepare import HERE,OUT,independent_filter
geo,markov,ensemble=source_modules(HERE)

def test_crisis_only_suppresses_the_hmm_component():
    idx=pd.date_range('2025-01-01',periods=2)
    signal=ensemble.ensemble_score(pd.Series([1.,1.],index=idx),pd.Series([.9,.9],index=idx),pd.Series([.51,.50],index=idx))
    np.testing.assert_array_equal(signal,[.5,.95])

@pytest.mark.parametrize('value,label',[(.65,'momentum'),(.35,'reversion'),(.5,'mixed')])
def test_source_threshold_boundaries(value,label):
    assert ensemble.regime_labels(pd.Series([value])).iloc[0]==label

def test_each_source_fold_starts_cash_after_source_shift():
    frame=pd.read_parquet(OUT/'native_model_features.parquet')
    first=frame.loc[frame.fold_reset]
    assert len(first)==6
    for c in['source','geometric','markov']:assert first['target_'+c].eq(0).all()
    assert frame.target_always.eq(1).all()

def test_saved_models_match_independent_forward_filter():
    capsule=json.loads((OUT/'model_capsule.json').read_text())
    for model in capsule['models']:
        assert not model['fallback']
        data=np.load(model['model_path'])
        np.testing.assert_allclose(independent_filter(data),data['probabilities'],atol=1e-10,rtol=0)
        np.testing.assert_allclose(data['probabilities'].sum(axis=1),1,atol=1e-12,rtol=0)

def test_source_geometric_test_only_warmup_is_preserved():
    x=pd.Series(np.full(20,.001),index=pd.date_range('2025-01-01',periods=20))
    actual=geo.geometric_signal(x,mom_thresh=.7,rev_thresh=.3)
    assert actual.iloc[:14].eq(0).all() and actual.iloc[14:].eq(1).all()

@pytest.mark.parametrize('exception',[ValueError,RuntimeError])
def test_callback_capture_rejects_logged_native_strategy_exception(exception):
    import logging
    from worker import CallbackErrors
    sys.path.insert(0,'/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
    from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
    h=CallbackErrors();logger=logging.getLogger('freqtrade.strategy.strategy_wrapper');logger.addHandler(h)
    def failing():raise exception('Intentional native callback boundary test')
    try:assert strategy_safe_wrapper(failing,supress_error=True)()is None
    finally:logger.removeHandler(h)
    assert len(h.errors)==1

@pytest.mark.parametrize('drift',['input','features','model'])
def test_frozen_model_cache_rejects_drift(tmp_path,drift):
    import shutil
    sys.path.insert(0,'/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType
    close=load_pair_history('BTC/USDT:USDT','1d',OUT/'candles',fill_up_missing=False,
                            drop_incomplete=False,candle_type=CandleType.FUTURES).set_index('date').close
    shutil.copyfile(OUT/'model_capsule.json',tmp_path/'model_capsule.json')
    shutil.copyfile(OUT/'native_model_features.parquet',tmp_path/'native_model_features.parquet')
    base,_=native_signals(close,HERE,tmp_path)
    assert len(base)>608
    if drift=='input':close=close.copy();close.iloc[-1]*=1.1
    elif drift=='features':(tmp_path/'native_model_features.parquet').write_bytes(b'tampered')
    else:
        data=json.loads((tmp_path/'model_capsule.json').read_text())
        model=tmp_path/'tampered.npz';model.write_bytes(b'tampered')
        data['models'][0]['model_path']=str(model)
        (tmp_path/'model_capsule.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='drift'):native_signals(close,HERE,tmp_path)
