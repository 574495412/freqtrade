"""Causal source risk allocation called inside actual native IStrategy.

No portfolio returns or wallet simulation. Source signals/vol methods are exact;
the covariance block is an explicit translation, with two frozen local controls.
"""
import math

import numpy as np
import pandas as pd
from AuthorMath import AuthorMath


def plan_weights(close,days,mode):
    if close.isna().any().any() or not np.isfinite(close).all().all() or close.le(0).any().any():
        raise ValueError('Require complete joint observed daily prices; no price imputation')
    if not close.index.is_unique or not close.index.is_monotonic_increasing:
        raise ValueError('Unique chronological daily prices required')
    if not close.index.equals(pd.date_range(close.index[0],close.index[-1],freq='D')):
        raise ValueError('Missing daily calendar, never compress')
    author=AuthorMath();signals=author.compute_tsmom_signals(close,1,0)['composite']
    volatility=author.compute_rolling_volatilities(close,1)
    returns=close.pct_change(fill_method=None).to_numpy(copy=True);returns[0]=0.  # exact source initialization, not an observed prior return
    weights=pd.DataFrame(0.,index=days,columns=close.columns);diagnostics=[]
    for day in days:
        # Source selects at row i using i-1 and applies weights to the NEXT
        # close-to-close return. Native executionT01 therefore uses closeT-2.
        evaluation=day-pd.Timedelta(days=2)
        if evaluation not in close.index:raise ValueError('Missing source execution-lag support')
        pos=close.index.get_loc(evaluation)
        if pos<365:raise ValueError('No full30/90/365 signals before funded window')
        sig=signals.iloc[pos].to_numpy();vol=volatility.iloc[pos].to_numpy()
        if not np.isfinite(vol).all() or (vol<=1e-4).any():raise ValueError('Invalid observed volatility')
        z=sig/vol;sample=returns[max(0,pos-1095):pos]
        covariance=np.cov(sample,rowvar=False)*365
        if mode=='covariance':covariance=.95*covariance+.05*np.diag(np.diag(covariance))
        elif mode=='diagonal':covariance=np.diag(np.diag(covariance))
        elif mode!='gross_one':raise ValueError('Unknown frozen allocation mode')
        variance=float(z@covariance@z)
        if mode=='gross_one':w=z/np.abs(z).sum() if np.abs(z).sum()>0 else z*0
        else:w=z*(.20/math.sqrt(variance)) if variance>1e-10 else z*0
        w=np.clip(w,-.5,.5)
        if np.abs(w).sum()>3:w*=3/np.abs(w).sum()
        weights.loc[day]=w
        diagnostics.append({'decision_date':day.isoformat(),'source_evaluation_date':evaluation.isoformat(),
            'covariance_observations':len(sample),'gross_target':float(np.abs(w).sum()),
            'net_target':float(w.sum()),'maximum_abs_weight':float(np.abs(w).max())})
    return weights,signals,volatility,diagnostics
