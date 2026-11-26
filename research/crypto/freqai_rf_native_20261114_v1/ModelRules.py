"""Explicit target maturity and intent rules; no independent account simulator."""
import numpy as np
import pandas as pd


def targets(frame):
    future=frame.close.shift(-20)
    valid=np.isfinite(frame.close)&np.isfinite(future)&frame.close.gt(0)&future.gt(0)
    result=pd.Series(pd.NA,index=frame.index,dtype='str')
    result.loc[valid]=np.where(future.loc[valid]>frame.close.loc[valid],'up','down')
    return result


def intents(frame,start):
    active=0;values=[]
    for row in frame.itertuples():
        if row.date<pd.Timestamp(start):active=0;values.append(0);continue
        if row.do_predict!=1 or not np.isfinite([row.up,row.down]).all() or min(row.up,row.down)<0 or max(row.up,row.down)>1 or abs(row.up+row.down-1)>1e-10:
            raise ValueError('Incomplete native prediction; no unknown-to-cash fallback')
        if active==0:
            if row.up>=.60:active=1
            elif row.down>=.60:active=-1
        elif active==1 and row.up<.50:active=0
        elif active==-1 and row.down<.50:active=0
        values.append(active)
    return np.array(values,dtype=int)
