"""Local research inputs for reference-spread copulas; never an account engine.

The pinned upstream Freqtrade engine loads this local extension explicitly through
IStrategy. No order, wallet, matching, funding or PnL implementation lives here.
Canonical valid copula rotations/Tawn kernels replace invalid printed boundary
identities, as separately documented; exact author-code replication is unknown.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import numpy as np
import pandas as pd

SYMBOLS=('BTC','ETH','BCH','XRP','EOS','LTC','TRX','ETC','LINK','XLM','ADA','XMR','DASH','ZEC','XTZ','ATOM','BNB','ONT','IOTA','BAT')


def reference_spreads(prices):
    """Formation-only raw-price OLS through origin, no intercept or log-price swap."""
    if list(prices.columns)!=list(SYMBOLS):raise ValueError('Full source20-column universe/order required')
    values=prices.to_numpy(dtype=float)
    if not np.isfinite(values).all()or (values<=0).any():raise ValueError('Complete positive formation prices required')
    x=values[:,1:];btc=values[:,0]
    beta=np.sum(x*btc[:,None],axis=0)/np.sum(x*x,axis=0)
    return pd.Series(beta,index=SYMBOLS[1:]),pd.DataFrame(btc[:,None]-x*beta,index=prices.index,columns=SYMBOLS[1:])


def h_probabilities(copula,uv):
    """Paper h1|2 is library hfunc2; reversing these swaps the actual coin legs."""
    uv=np.asfortranarray(uv,dtype=float)
    if uv.ndim!=2 or uv.shape[1]!=2 or not np.isfinite(uv).all()or (uv<=0).any()or (uv>=1).any():raise ValueError('PIT must be finite and strictly inside unit square')
    h12,h21=copula.hfunc2(uv),copula.hfunc1(uv)
    if not np.isfinite(h12).all()or not np.isfinite(h21).all():raise ValueError('Unknown conditional probability')
    return h12,h21


def pair_states(h12,h21,alpha_open,alpha_close=.1):
    """Table3 spread states; no same-bar reversal or cumulative-index substitution."""
    if alpha_open not in (.1,.15,.2)or alpha_close!=.1:raise ValueError('Unregistered source thresholds')
    h12,h21=np.asarray(h12,dtype=float),np.asarray(h21,dtype=float)
    if h12.shape!=h21.shape or not np.isfinite(h12).all()or not np.isfinite(h21).all():raise ValueError('Unknown/misaligned conditional probabilities')
    if ((h12<0)|(h12>1)|(h21<0)|(h21>1)).any():raise ValueError('Conditional probability outside unit interval')
    state=0;out=[]
    for a,b in zip(h12,h21,strict=True):
        if state:
            if abs(a-.5)<alpha_close and abs(b-.5)<alpha_close:state=0
        elif a<alpha_open and b>1-alpha_open:state=1
        elif a>1-alpha_open and b<alpha_open:state=-1
        out.append(state)
    return np.asarray(out,dtype=np.int8)


def coin_units(spread_state,beta1,beta2):
    """Table4 algebra before wallet normalization: BTC cancels exactly."""
    if spread_state not in (-1,0,1)or not np.isfinite([beta1,beta2]).all()or min(beta1,beta2)<=0:raise ValueError('Invalid source spread quantities')
    return {'BTC':0.,'coin1':-spread_state*beta1,'coin2':spread_state*beta2}


def tradable_mask(index,symbol,terminal=None):
    """Keep a terminated symbol in the universe; do not invent postterminal prices."""
    index=pd.DatetimeIndex(index)
    if symbol not in SYMBOLS:raise ValueError('Symbol outside source universe')
    if terminal is None:return np.ones(len(index),dtype=bool)
    if symbol!='EOS':raise ValueError('No terminal evidence for this symbol')
    terminal=pd.Timestamp(terminal)
    if terminal.tz is None:raise ValueError('Terminal timezone required')
    return np.asarray(index<terminal)


def family_grid(pv):
    base=['gaussian','student','clayton','gumbel','frank','joe','bb1','bb6','bb7','bb8']
    rows=[]
    for family in base:
        for rotation in ([0]if family in ['gaussian','student','frank']else[0,90,180,270]):
            rows.append((family,rotation))
    return rows+[(name,r)for name in ['tawn_alpha','tawn_beta']for r in [0,90,180,270]]


def canonical_tawn(pv,asymmetry,theta,orientation,rotation=0):
    if orientation not in ('tawn_alpha','tawn_beta'):raise ValueError('Unknown constrained Tawn orientation')
    params=[asymmetry,1.,theta]if orientation=='tawn_alpha'else[1.,asymmetry,theta]
    return pv.Bicop(family=pv.BicopFamily.tawn,rotation=rotation,parameters=np.array(params).reshape(3,1))


def tawn_cdf(uv,alpha,beta,theta):
    """Independent canonical asymmetric-logistic CDF with valid uniform margins."""
    uv=np.asarray(uv,dtype=float);x,y=-np.log(uv[:,0]),-np.log(uv[:,1])
    return np.exp(-(1-alpha)*x-(1-beta)*y-((alpha*x)**theta+(beta*y)**theta)**(1/theta))


def formation_spreads(prices,asof,terminal):
    """504 completed hours; preserve all source columns and dated venue exclusion."""
    if list(prices.columns)!=list(SYMBOLS):raise ValueError('Full source20universe required')
    asof=pd.Timestamp(asof)
    window=prices.loc[(prices.index>=asof-pd.Timedelta(hours=504))&(prices.index<asof)]
    expected=pd.date_range(asof-pd.Timedelta(hours=504),asof,freq='h',inclusive='left')
    if not window.index.as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Incomplete exact formation clock')
    active=[s for s in SYMBOLS if s=='BTC'or tradable_mask(pd.DatetimeIndex([asof]),s,terminal if s=='EOS'else None)[0]]
    selected=window[active]
    values=selected.to_numpy(dtype=float)
    if not np.isfinite(values).all()or (values<=0).any():raise ValueError('Unknown quote for active source member')
    btc=values[:,0];x=values[:,1:]
    beta=np.sum(x*btc[:,None],axis=0)/np.sum(x*x,axis=0)
    return pd.Series(beta,index=active[1:]),pd.DataFrame(btc[:,None]-x*beta,index=window.index,columns=active[1:]),{'source_members':list(SYMBOLS),'active_members':active,'inactive_members':[s for s in SYMBOLS if s not in active],'asof':str(asof),'first_completed_bar':str(expected[0]),'last_completed_bar':str(expected[-1]),'rows':504,'not_a_stationarity_or_financial_result':True}


def fit_copulas(pv,uv):
    """Source-complete39candidate MLE/AIC, with two-parameter constrained Tawn.

    This numerical recipe is a local adaptation, not recovered author code.
    All candidate outcomes are retained; no absent-family five-copula fallback.
    """
    from scipy.optimize import minimize
    uv=np.asfortranarray(uv,dtype=float)
    if uv.ndim!=2 or uv.shape[1]!=2 or not np.isfinite(uv).all()or (uv<=0).any()or (uv>=1).any():raise ValueError('Invalid formation PIT')
    records=[];models=[]
    controls=pv.FitControlsBicop(parametric_method='mle',selection_criterion='aic',preselect_families=False,allow_rotations=False,num_threads=1)
    for family,rotation in family_grid(pv):
        if family.startswith('tawn_'):
            def loss(parameters):
                model=canonical_tawn(pv,float(parameters[0]),float(parameters[1]),family,rotation)
                pdf=model.pdf(uv)
                if not np.isfinite(pdf).all()or (pdf<=0).any():return 1e100
                return -float(np.log(pdf).sum())
            attempts=[];successful=[]
            for start in [(.3,1.5),(.7,2.),(.95,5.)]:
                fit=minimize(loss,start,method='L-BFGS-B',bounds=[(1e-6,1.),(1.00001,60.)],options={'maxiter':500,'ftol':1e-10,'gtol':1e-6})
                attempt={'start':list(start),'success':bool(fit.success),'message':str(fit.message),'parameters':fit.x.tolist(),'negative_loglik':float(fit.fun)};attempts.append(attempt)
                if fit.success and np.isfinite(fit.fun)and fit.fun<1e99:successful.append(fit)
            if not successful:raise ValueError('Full family admission failed for '+family+str(rotation)+':'+str(attempts))
            chosen=min(successful,key=lambda f:f.fun);model=canonical_tawn(pv,float(chosen.x[0]),float(chosen.x[1]),family,rotation);parameters=2
        else:
            model=pv.Bicop(family=getattr(pv.BicopFamily,family),rotation=rotation);model.fit(uv,controls=controls);parameters=int(model.npars);attempts=None
        density=model.pdf(uv)
        if not np.isfinite(density).all()or (density<=0).any():raise ValueError('Unknown fitted density')
        loglik=float(np.log(density).sum())
        if abs(loglik-model.loglik(uv))>1e-7:raise ValueError('Independent density/loglik mismatch')
        records.append({'family':family,'rotation':rotation,'parameters':model.parameters.tolist(),'free_parameters':parameters,'training_loglik':loglik,'aic':2*parameters-2*loglik,'optimizer_attempts':attempts})
        models.append(model)
    selected=min(range(len(records)),key=lambda i:(records[i]['aic'],i))
    return models[selected],{'selected':records[selected],'all_candidates':records,'selection_index':selected,'formation_rows':len(uv),'local_optimizer_recipe':True,'no_account_returns':True}
