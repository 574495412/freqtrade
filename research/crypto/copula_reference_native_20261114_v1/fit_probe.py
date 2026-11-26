"""Three frozen native-formation model checks, not a funded strategy screen."""
import importlib.util,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from .prepare import HERE,ROOT,OUT,save,sha

def main():
    assert json.loads((HERE/'native_preflight.json').read_text())['passed_within_scope']
    sys.path.insert(0,str(HERE/'dependencies'))
    import pyvinecopulib as pv
    spec=importlib.util.spec_from_file_location('native_copula_formation',ROOT/'freqtrade/data/copula_reference.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    # Use the ACTUAL native admission prices reconstructed from the same loader
    # domain verified by CopulaInputs. This is an estimator diagnostic only;
    # it does not select tradable assets, alter source ADF or calculate PnL.
    prices=pd.DataFrame({s:pd.read_feather(OUT/f'candles/futures/{s}_USDT_USDT-1h-futures.feather').set_index('date').close for s in m.SYMBOLS})
    records=[]
    for edge in ['2025-01-03T00:00Z','2025-06-06T00:00Z','2026-08-28T00:00Z']:
        asof=pd.Timestamp(edge);beta,spread,diag=m.formation_spreads(prices,asof,pd.Timestamp('2025-05-21 09:00',tz='UTC'))
        # A fixed ETH/BCH reference pair admits the estimator independent of
        # economic output; these are not claimed source-selected trading pairs.
        pair=['ETH','BCH'];marginals=[];u=[]
        for symbol in pair:
            x=spread[symbol].to_numpy();fits=[]
            for family,k in [('norm',2),('t',3),('cauchy',2)]:
                distribution=getattr(stats,family);params=distribution.fit(x);loglik=float(distribution.logpdf(x,*params).sum());fits.append({'family':family,'parameters':list(params),'loglik':loglik,'aic':2*k-2*loglik})
            selected=min(fits,key=lambda f:f['aic']);d=getattr(stats,selected['family']);u.append(np.clip(d.cdf(x,*selected['parameters']),1e-10,1-1e-10));marginals.append({'symbol':symbol,'selected':selected,'all_candidates':fits})
        uv=np.column_stack(u);model,fit=m.fit_copulas(pv,uv);h12,h21=m.h_probabilities(model,uv)
        assert np.isfinite(h12).all()and np.isfinite(h21).all()
        save(OUT/f'fit_{asof.strftime("%Y%m%d")}.json',{'formation':diag,'fixed_diagnostic_pair':pair,'not_source_cointegration_selected':True,'marginals':marginals,'copula':fit,'model_json':model.to_json(),'native_owner_sha256':sha(ROOT/'freqtrade/data/copula_reference.py'),'no_account_returns':True})
        records.append({'asof':edge,'selected':fit['selected'],'candidates':len(fit['all_candidates']),'conditional_cells':len(uv)*2})
        save(HERE/'fit_probe_checkpoint.json',{'records':records,'expected':3,'financial_accounts':0});print('FIT',edge,fit['selected']['family'],fit['selected']['rotation'],flush=True)
    save(HERE/'fit_probe.json',{'passed_within_scope':True,'records':records,'fitted_diagnostic_models':3,'fitted_candidate_models':sum(r['candidates']for r in records),'conditional_cells':sum(r['conditional_cells']for r in records),'source_selected_trading_pairs':False,'full_rolling_strategy':False,'native_funded_accounts':0})
if __name__=='__main__':main()
