"""Supported native FreqAI model subclasses and read-only training evidence.

The RF estimator and native pipelines remain upstream. Its string predictions
already carry class names, so BaseClassifierModel is the correct predictor.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier

from freqtrade.enums import RunMode
from freqtrade.freqai.base_models.BaseClassifierModel import BaseClassifierModel
from freqtrade.freqai.prediction_models.SKLearnRandomForestClassifier import SKLearnRandomForestClassifier


class AuditedRF(SKLearnRandomForestClassifier):
    prior=False

    def assert_config(self,config):
        super().assert_config(config)
        if config.get('runmode')!=RunMode.BACKTEST:raise ValueError('Native research models are BACKTEST only')

    def train(self,unfiltered_df,pair,dk,**kwargs):
        mature=unfiltered_df.loc[unfiltered_df['&-direction'].notna(),'date']
        stop=self.training_timerange.stopdt
        if mature.empty or (mature+pd.Timedelta(hours=21)).gt(stop).any():
            raise ValueError('Unmatured classifier target entered training')
        self._audit_stop=stop;self._audit_raw_mature_dates=mature
        return super().train(unfiltered_df,pair,dk,**kwargs)

    def fit(self,data_dictionary,dk,**kwargs):
        if self.prior:
            model=DummyClassifier(strategy='prior')
            model.fit(data_dictionary['train_features'].to_numpy(),data_dictionary['train_labels'].iloc[:,0].to_numpy(),sample_weight=data_dictionary['train_weights'])
        else:model=super().fit(data_dictionary,dk,**kwargs)
        if list(model.classes_)!=['down','up']:raise ValueError('Both original string classes required')
        dates=dk.train_dates.reset_index(drop=True)
        matrix=data_dictionary['train_features'].reset_index(drop=True).copy()
        if len(matrix)!=len(dates):raise ValueError('Training dates/rows mismatch')
        matrix.insert(0,'date',dates)
        matrix['label']=data_dictionary['train_labels'].iloc[:,0].reset_index(drop=True)
        self._audit_dir=Path(self.config['user_data_dir'])/'freqai_audit'
        self._audit_dir.mkdir(exist_ok=True,parents=True)
        self._audit_key=str(int(self._audit_stop.timestamp()))
        matrix.to_parquet(self._audit_dir/f'train_{self._audit_key}.parquet',index=False)
        arrays={}
        if not self.prior:
            for i,tree in enumerate(model.estimators_):
                t=tree.tree_
                for key,value in [('left',t.children_left),('right',t.children_right),('feature',t.feature),('threshold',t.threshold),('value',t.value[:,0,:])]:
                    arrays[f'{key}_{i}']=value
            np.savez_compressed(self._audit_dir/f'trees_{self._audit_key}.npz',**arrays)
        self._audit_record=dict(train_stop=str(self._audit_stop),training_rows=len(matrix),
            feature_count=len(data_dictionary['train_features'].columns),latest_training_date=str(dates.max()),
            latest_label_known_at=str(dates.max()+pd.Timedelta(hours=21)),mature_label_check_passed=True,
            classes=list(model.classes_),prior=self.prior,class_prior=(model.class_prior_.tolist() if self.prior else None),
            native_training_pipeline=True,native_rf_fit=not self.prior,full_source_label_inverse_transform_skipped=True)
        return model

    def predict(self,unfiltered_df,dk,**kwargs):
        result,valid=BaseClassifierModel.predict(self,unfiltered_df,dk,**kwargs)
        x=dk.data_dictionary['prediction_features'].to_numpy()
        if self.prior:independent=np.tile(self.model.class_prior_,(len(x),1))
        else:
            # sklearn tree traversal uses float32 feature comparisons.
            inputs=x.astype(np.float32);independent=np.zeros((len(x),2))
            for estimator in self.model.estimators_:
                tree=estimator.tree_;nodes=np.zeros(len(inputs),dtype=int)
                running=tree.children_left[nodes]>=0
                while running.any():
                    ids=np.flatnonzero(running);n=nodes[ids]
                    choose=inputs[ids,tree.feature[n]]<=tree.threshold[n]
                    nodes[ids]=np.where(choose,tree.children_left[n],tree.children_right[n])
                    running=tree.children_left[nodes]>=0
                value=tree.value[nodes,0,:];independent+=value/value.sum(axis=1,keepdims=True)
            independent/=len(self.model.estimators_)
        maximum=float(np.max(np.abs(independent-result[['down','up']].to_numpy())))
        if maximum>1e-12:raise ValueError('Independent native tree/probability reconstruction failed')
        saved=pd.DataFrame(x,columns=dk.data_dictionary['prediction_features'].columns)
        saved.insert(0,'date',unfiltered_df.date.to_numpy())
        saved['up']=result.up.to_numpy();saved['down']=result.down.to_numpy();saved['do_predict']=valid
        saved.to_parquet(self._audit_dir/f'predict_{self._audit_key}.parquet',index=False)
        record={**self._audit_record,'prediction_rows':len(result),'maximum_probability_error':maximum,
                'independent_prediction_reconciliation_passed':True}
        (self._audit_dir/f'model_{self._audit_key}.json').write_text(json.dumps(record,indent=2)+'\n')
        return result,valid


class TrainingPrior(AuditedRF):prior=True
