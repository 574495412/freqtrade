"""Actual native RF fit and DataKitchen prediction; synthetic component only."""

import json
from pathlib import Path

from bootstrap import boot

HERE=Path(__file__).resolve().parent


def main():
    manifest=boot()
    import numpy as np
    import pandas as pd
    from datasieve.pipeline import Pipeline
    from datasieve.transforms import SKLearnWrapper
    from sklearn.preprocessing import MinMaxScaler
    from freqtrade.freqai.base_models.BaseClassifierModel import BaseClassifierModel
    from freqtrade.freqai.data_kitchen import FreqaiDataKitchen
    from freqtrade.freqai.prediction_models.SKLearnRandomForestClassifier import SKLearnRandomForestClassifier
    config=dict(user_data_dir=HERE/'component',timeframe='1h',timerange='20250101-20250108',
                config_files=[str(HERE/'component_config.json')],exchange=dict(pair_whitelist=['BTC/USDT:USDT']),
                freqai=dict(identifier='native_rf_probe',train_period_days=30,backtest_period_days=7,
                            feature_parameters=dict(include_corr_pairlist=[],weight_factor=0,shuffle_after_split=False)))
    (HERE/'component_config.json').write_text(json.dumps(config,default=str,indent=2))
    dk=FreqaiDataKitchen(config,live=False,pair='BTC/USDT:USDT')
    features=pd.DataFrame({'%-x':np.arange(12,dtype=float),'%-y':np.arange(12,dtype=float)**2})
    labels=pd.DataFrame({'&-direction':['down']*6+['up']*6})
    dk.training_features_list=list(features);dk.label_list=list(labels)
    dk.unique_class_list=['down','up'];dk.data_dictionary={'train_features':features,'train_labels':labels}
    dk.fit_labels()
    dk.feature_pipeline=Pipeline([('scaler',SKLearnWrapper(MinMaxScaler(feature_range=(-1,1))))])
    scaled,_,_=dk.feature_pipeline.fit_transform(features,labels,np.ones(12))
    data=dict(train_features=scaled,train_labels=labels,train_weights=np.ones(12))
    model=object.__new__(SKLearnRandomForestClassifier)
    model.freqai_info={'data_split_parameters':{'test_size':0}}
    model.model_training_parameters=dict(n_estimators=3,max_depth=2,n_jobs=1,random_state=42)
    model.CONV_WIDTH=1;model.model=model.fit(data,dk)
    error=None
    try:model.predict(features,dk)
    except (ValueError,TypeError,IndexError) as exc:error=dict(type=type(exc).__name__,message=str(exc))
    assert error is not None,'Do not apply an unproven label conversion repair'
    corrected,valid=BaseClassifierModel.predict(model,features,dk)
    direct=model.model.predict_proba(scaled.to_numpy())
    assert corrected['&-direction'].tolist()==model.model.predict(scaled.to_numpy()).tolist()
    np.testing.assert_array_equal(corrected[list(model.model.classes_)].to_numpy(),direct)
    assert (valid==1).all()
    result=dict(synthetic=True,financial_accounts=0,native_source_files_verified=len(manifest['files']),
                original_source_predict_error=error,actual_native_fit=True,actual_native_datakitchen=True,
                native_base_predict_correct=True,classes=model.model.classes_.tolist(),
                identical_estimator_and_pipeline=True,probability_cells=direct.size,
                fix_scope='Supported subclass calls BaseClassifierModel.predict directly; RF already predicts original string classes, so no second inverse transform')
    (HERE/'classifier_component_probe.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
