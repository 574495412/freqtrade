"""Local rolling source nominations, not returns or a separate trading engine."""
import numpy as np
import pandas as pd
from scipy.stats import kendalltau
from statsmodels.tsa.stattools import adfuller


def nominate(prices,asof,terminal,reference):
    beta,spreads,diagnostics=reference.formation_spreads(prices,asof,terminal)
    records=[];passed=[]
    for order,symbol in enumerate(beta.index):
        spread=spreads[symbol].to_numpy(dtype=float)
        statistic,pvalue,usedlag,nobs,critical,icbest=adfuller(spread,regression='c',autolag='AIC')
        values=prices.loc[spreads.index]
        tau=float(kendalltau(values.BTC,values[symbol]).statistic)
        if not np.isfinite([statistic,pvalue,tau]).all():raise ValueError('Unknown source formation statistic')
        row={'symbol':symbol,'source_order':order,'beta':float(beta[symbol]),'adf_statistic':float(statistic),'adf_pvalue':float(pvalue),'adf_usedlag':int(usedlag),'adf_nobs':int(nobs),'adf_critical':critical,'adf_icbest':float(icbest),'kendall_tau':tau,'passes_source_adf':bool(pvalue<.1)}
        records.append(row)
        if row['passes_source_adf']:passed.append(row)
    ranked=sorted(passed,key=lambda r:(-r['kendall_tau'],r['source_order']))
    selected=[r['symbol']for r in ranked[:2]]if len(ranked)>=2 else[]
    return {'formation':diagnostics,'statistics':records,'passing_members':len(passed),'selected':selected,'no_trading_week':not selected,'source_adjusted_EG_certified':False,'no_account_returns':True}


def fit_week(prices,nomination,pv,reference):
    """Train only on the fixed past formation window; no wallet or return model."""
    from scipy import stats
    selected=nomination['selected']
    if not selected:return {'status':'INSUFFICIENT_STATIONARITY','nomination':nomination,'native_accounts':0},None
    asof=pd.Timestamp(nomination['formation']['asof']);window=prices.loc[(prices.index>=asof-pd.Timedelta(hours=504))&(prices.index<asof)]
    assert len(window)==504
    beta={r['symbol']:r['beta']for r in nomination['statistics']};uv=[];marginals=[]
    for symbol in selected:
        x=(window.BTC-beta[symbol]*window[symbol]).to_numpy(dtype=float);fits=[]
        if not np.isfinite(x).all():raise ValueError('Unknown selected formation spread')
        for name,k in [('norm',2),('t',3),('cauchy',2)]:
            d=getattr(stats,name);parameters=d.fit(x);loglik=float(d.logpdf(x,*parameters).sum())
            if not np.isfinite(loglik):raise ValueError('Nonfinite marginal likelihood')
            fits.append({'family':name,'parameters':list(parameters),'loglik':loglik,'aic':2*k-2*loglik})
        best=min(fits,key=lambda r:r['aic']);d=getattr(stats,best['family']);uv.append(np.clip(d.cdf(x,*best['parameters']),1e-10,1-1e-10));marginals.append({'symbol':symbol,'all_candidates':fits,'selected':best})
    model,candidates=reference.fit_copulas(pv,np.column_stack(uv))
    return {'status':'FITTED','nomination':nomination,'marginals':marginals,'copula':candidates,'model_json':model.to_json(),'native_accounts':0},model


def forecast_week(prices,record,model,pv,reference,start,end):
    """Decision atT uses only the already-completed hourly close labelledT-1h."""
    from scipy import stats
    cutoff=pd.Timestamp(record['nomination']['formation']['asof']);begin=max(cutoff,pd.Timestamp(start));finish=min(cutoff+pd.Timedelta(days=7),pd.Timestamp(end))
    times=pd.date_range(begin,finish,freq='h',inclusive='left');frame=pd.DataFrame(index=times);frame.index.name='decision_time';frame['week']=cutoff.isoformat();frame['model_status']=record['status']
    for alpha in [10,15,20]:frame[f'state_{alpha}']=0
    if record['status']!='FITTED':return frame
    selected=record['nomination']['selected'];beta={r['symbol']:r['beta']for r in record['nomination']['statistics']};clocks=times-pd.Timedelta(hours=1);uv=[]
    for marginal in record['marginals']:
        symbol=marginal['symbol'];x=(prices.BTC.reindex(clocks)-beta[symbol]*prices[symbol].reindex(clocks)).to_numpy(dtype=float)
        if not np.isfinite(x).all():raise ValueError('Selected live spread UNKNOWN; no omission, cash proxy or terminal quote fill')
        best=marginal['selected'];uv.append(np.clip(getattr(stats,best['family']).cdf(x,*best['parameters']),1e-10,1-1e-10))
    h12,h21=reference.h_probabilities(model,np.column_stack(uv));frame['h12']=h12;frame['h21']=h21;frame['symbol1']=selected[0];frame['symbol2']=selected[1];frame['beta1']=beta[selected[0]];frame['beta2']=beta[selected[1]]
    for alpha in [.1,.15,.2]:frame[f'state_{int(alpha*100)}']=reference.pair_states(h12,h21,alpha)
    return frame
