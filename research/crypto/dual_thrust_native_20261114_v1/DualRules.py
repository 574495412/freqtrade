"""Local UTC, hourly execution adaptation of the captured BTC alpha rules."""

import numpy as np
import pandas as pd


def completed_states(frame, mode, start='2025-01-01T00:00:00Z'):
    f=frame.copy().reset_index(drop=True)
    dates=pd.DatetimeIndex(f.date).tz_convert('UTC').as_unit('ns')
    if not dates.equals(pd.date_range(dates[0],dates[-1],freq='h')):
        raise ValueError('Complete ordered hourly grid required; no missing-bar fill')
    values=f[['open','high','low','close','volume']].to_numpy(float)
    if not np.isfinite(values).all() or (values<=0).any():raise ValueError('Invalid OHLCV input')
    if (f.low>f[['open','close']].min(axis=1)).any() or (f.high<f[['open','close']].max(axis=1)).any():
        raise ValueError('OHLC bounds conflict')
    f['clock']=dates+pd.Timedelta(hours=1)
    f['day']=dates.floor('D')
    daily=f.groupby('day').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),count=('close','size'))
    daily=daily.loc[daily['count'].eq(24)]
    if not daily.index.equals(pd.date_range(daily.index[0],daily.index[-1],freq='D')):
        raise ValueError('Incomplete interior UTC day')
    hh=daily.high.rolling(4,min_periods=4).max();lc=daily.close.rolling(4,min_periods=4).min()
    hc=daily.close.rolling(4,min_periods=4).max();ll=daily.low.rolling(4,min_periods=4).min()
    daily['range']=np.maximum(hh-lc,hc-ll)
    daily['upper']=daily.open+.5*daily['range']
    daily['lower']=daily.open-.5*daily['range']
    daily['sma_ratio']=daily.close.rolling(10,min_periods=10).mean()/daily.close.rolling(20,min_periods=20).mean()-1
    day=f.clock.dt.floor('D')-pd.Timedelta(days=1)
    for column in ['upper','lower','range','sma_ratio']:
        f[column]=daily[column].reindex(pd.DatetimeIndex(day)).to_numpy()
    f['metric_day']=day;f['source_signal']=0.;f['target_weight']=0.
    funded=f.clock.ge(pd.Timestamp(start))
    required=['upper','lower'] if mode=='dual' else ['sma_ratio'] if mode=='sma' else []
    if required and not np.isfinite(f.loc[funded,required]).all().all():
        raise ValueError('Insufficient completed-day warmup')
    if mode=='dual':
        active=False;expires=None;states=[];events=[]
        for row in f.itertuples():
            event=0
            if row.clock>=pd.Timestamp(start):
                if expires is not None and row.clock>=expires:active=False
                if row.close>=row.upper:
                    active=True;expires=row.clock+pd.Timedelta(days=365);event=1
                elif row.close<row.lower:active=False;expires=None;event=-1
            states.append(int(active));events.append(event)
        f['target_weight']=states;f['source_signal']=events
    elif mode=='sma':
        f['target_weight']=(funded&f.sma_ratio.gt(0)).astype(int)
        f['source_signal']=f.target_weight
    elif mode=='hold':
        f['target_weight']=funded.astype(int);f['source_signal']=f.target_weight
    else:raise ValueError('Unknown frozen mode')
    f['source_volatility']=np.nan
    f['decision_date']=f.clock
    return f.drop(columns=['day']),daily
