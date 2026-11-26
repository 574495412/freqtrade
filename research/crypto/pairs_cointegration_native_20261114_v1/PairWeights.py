"""Local native-called source pair mathematics; no account or return simulator."""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DEPENDENCIES=Path('/Users/chrise/GitHub/freqtrade/research/crypto/pairs_cointegration_native_20261114_v1/dependencies')
sys.path.insert(0,str(DEPENDENCIES))
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import adfuller,coint

SYMBOLS=['BTC','ETH','BNB','SOL','ADA','XRP','LTC','DOT','LINK','TRX']


def validate_prices(close):
    if list(close.columns)!=SYMBOLS:raise ValueError('Frozen source symbol order required')
    if not close.index.equals(pd.date_range(close.index[0],close.index[-1],freq='D')):
        raise ValueError('Complete daily grid required; never compress missing dates')
    if close.isna().any().any() or not np.isfinite(close).all().all() or close.le(0).any().any():
        raise ValueError('Complete positive observed prices required')


def fit_pairs(close,mode):
    validate_prices(close)
    train=close.loc['2020-08-18':'2024-01-16']
    if len(train)!=1247:raise ValueError('Exact source1247-day training window required')
    logs=np.log(train);rows=[]
    if mode not in ('naive','adjusted'):raise ValueError('Unknown predeclared selection')
    for a,b in itertools.combinations(SYMBOLS,2):
        alternatives=[]
        for dependent,independent in [(a,b),(b,a)]:
            y,x=logs[dependent].to_numpy(),logs[independent].to_numpy()
            model=OLS(y,add_constant(x)).fit();alpha,beta=map(float,model.params)
            residual=y-alpha-beta*x
            if mode=='naive':
                statistic,pvalue,*_=adfuller(residual,autolag='AIC')
            else:
                statistic,pvalue,_=coint(y,x,trend='c',autolag='AIC')
            alternatives.append(dict(dependent=dependent,independent=independent,alpha=alpha,
                                     beta=beta,pvalue=float(pvalue),statistic=float(statistic)))
        best=min(alternatives,key=lambda r:r['pvalue'])
        rows.append({**best,'other_direction':alternatives[1] if best is alternatives[0] else alternatives[0]})
    rows.sort(key=lambda r:r['pvalue'])
    return rows,[r for r in rows if r['pvalue']<.05]


def states_from_z(z):
    # Independently expressed source state machine: strict thresholds and no
    # same-bar reversal, including its entry beyond4 while previously flat.
    state=0;states=[]
    for value in z:
        if not np.isnan(value):
            if state==0:
                if value>2:state=-1
                elif value<-2:state=1
            elif abs(value)>4 or abs(value)<.5:state=0
        states.append(state)
    return np.asarray(states,dtype=int)


def pair_weights(close,days,mode):
    all_pairs,selected=fit_pairs(close,mode)
    evaluation=days-pd.Timedelta(days=1)
    if not evaluation.isin(close.index).all():raise ValueError('Missing completed signal close')
    weights=pd.DataFrame(0.,index=days,columns=SYMBOLS);state_rows=[]
    logs=np.log(close)
    for model in selected:
        dep,indep=model['dependent'],model['independent'];alpha,beta=model['alpha'],model['beta']
        spread=logs[dep]-alpha-beta*logs[indep]
        z=(spread-spread.rolling(30,min_periods=30).mean())/spread.rolling(30,min_periods=30).std(ddof=1)
        observed=z.reindex(evaluation)
        if not np.isfinite(observed).all():raise ValueError('Unknown/zero-variance live pair zscore')
        states=states_from_z(observed.to_numpy())
        fraction=1/(len(selected)*(1+abs(beta)))
        weights[dep]+=states*fraction;weights[indep]-=states*beta*fraction
        state_rows.append(pd.DataFrame(dict(decision_date=days,dependent=dep,independent=indep,
                                            z=observed.to_numpy(),state=states)))
    if weights.abs().sum(axis=1).gt(1+1e-12).any():raise ValueError('Gross pair budget exceeded')
    panel=pd.concat(state_rows,ignore_index=True) if state_rows else pd.DataFrame()
    diagnostics=dict(mode=mode,all_train_pairs=all_pairs,selected_pairs=selected,
                     selected_count=len(selected),decision_days=len(days),
                     method='Source daily signals, local equal fixed pair budgets and native netted quantities',
                     states=panel.to_dict('records'),gross_target=weights.abs().sum(axis=1).tolist(),
                     net_target=weights.sum(axis=1).tolist(),source_volatility_field='Not applicable; exported NaN')
    return weights,diagnostics,panel
