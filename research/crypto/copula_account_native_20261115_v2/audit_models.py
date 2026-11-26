"""Independent saved-model/clock/causality audits, never an account simulator."""
import importlib.util,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from .nominate import HERE,ROOT,OLD,OUT
from .fit_native import sha

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def tau_b(x,y):
    upper=np.triu(np.ones((len(x),len(x)),dtype=bool),1)
    a=np.sign(x[:,None]-x[None,:])[upper];b=np.sign(y[:,None]-y[None,:])[upper]
    return float(np.sum(a*b)/np.sqrt(np.count_nonzero(a)*np.count_nonzero(b)))

def main():
    capsule=json.loads((OUT/'model_capsule.json').read_text());assert capsule['weekly_records']==88
    for path,digest in capsule['identity'].items():assert sha(path)==digest,path
    for r in capsule['all_model_files']:assert sha(r['path'])==r['sha256']
    assert sha(capsule['forecasts']['path'])==capsule['forecasts']['sha256']
    sys.path.insert(0,str(OLD/'dependencies'));sys.path.insert(0,str(ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/dependencies'))
    import pyvinecopulib as pv
    ref=load('audit_copula_reference',ROOT/'freqtrade/data/copula_reference.py');form=load('audit_copula_formation',ROOT/'freqtrade/data/copula_formation.py')
    prices=pd.DataFrame({s:pd.read_feather(ROOT/f'user_data/crypto_quant/copula_reference_native_20261114_v1/candles/futures/{s}_USDT_USDT-1h-futures.feather').set_index('date').close for s in ref.SYMBOLS})
    forecasts=pd.read_parquet(capsule['forecasts']['path']);grid=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h').as_unit('ns');assert forecasts.index.as_unit('ns').equals(grid)
    records=[];ncoeff=ncop=nmarg=nstates=0;maxbeta=maxtau=maxlik=maxh=0.
    allrecords={}
    for r in capsule['all_model_files']:
        record=json.loads(Path(r['path']).read_text());nom=record['nomination'];asof=pd.Timestamp(nom['formation']['asof']);allrecords[asof]=record
        window=prices.loc[(prices.index>=asof-pd.Timedelta(hours=504))&(prices.index<asof)];assert len(window)==504
        passing=[]
        for row in nom['statistics']:
            symbol=row['symbol'];x=window[symbol].to_numpy();btc=window.BTC.to_numpy();b=float(np.linalg.lstsq(x[:,None],btc,rcond=None)[0][0]);error=abs(b-row['beta']);maxbeta=max(maxbeta,error);assert error<1e-9*max(1,abs(b))
            tau=tau_b(btc,x);error=abs(tau-row['kendall_tau']);maxtau=max(maxtau,error);assert error<1e-12
            assert row['passes_source_adf']==(row['adf_pvalue']<.1)
            if row['passes_source_adf']:passing.append(row)
            ncoeff+=1
        ranked=sorted(passing,key=lambda row:(-row['kendall_tau'],row['source_order']));selected=[row['symbol']for row in ranked[:2]]if len(ranked)>=2 else[];assert selected==nom['selected']
        if record['status']=='INSUFFICIENT_STATIONARITY':assert not selected;model=None
        else:
            assert len(selected)==2;uv=[];betas={row['symbol']:row['beta']for row in nom['statistics']}
            for marginal in record['marginals']:
                symbol=marginal['symbol'];x=(window.BTC-betas[symbol]*window[symbol]).to_numpy()
                for candidate in marginal['all_candidates']:
                    ll=float(getattr(stats,candidate['family']).logpdf(x,*candidate['parameters']).sum());error=abs(ll-candidate['loglik']);maxlik=max(maxlik,error);assert error<1e-7;nmarg+=1
                best=min(marginal['all_candidates'],key=lambda a:a['aic']);assert best==marginal['selected'];uv.append(np.clip(getattr(stats,best['family']).cdf(x,*best['parameters']),1e-10,1-1e-10))
            uv=np.asfortranarray(np.column_stack(uv));cs=record['copula']['all_candidates'];assert [(c['family'],c['rotation'])for c in cs]==ref.family_grid(pv)
            for candidate in cs:
                family=pv.BicopFamily.tawn if candidate['family'].startswith('tawn_')else getattr(pv.BicopFamily,candidate['family']);c=pv.Bicop(family=family,rotation=candidate['rotation'],parameters=np.array(candidate['parameters']))
                ll=float(np.log(c.pdf(uv)).sum());error=abs(ll-candidate['training_loglik']);maxlik=max(maxlik,error);assert error<1e-7
                k=2 if candidate['family'].startswith('tawn_')else int(c.npars);assert k==candidate['free_parameters']and abs(2*k-2*ll-candidate['aic'])<1e-7;ncop+=1
            idx=min(range(len(cs)),key=lambda i:(cs[i]['aic'],i));assert idx==record['copula']['selection_index'];model=pv.Bicop.from_json(record['model_json'])
        # Replay the saved model's clock only; no fitting on the trading window.
        expected=form.forecast_week(prices,record,model,pv,ref,'2025-01-01T00:00Z','2026-09-01T00:00Z');actual=forecasts.loc[expected.index,expected.columns]
        pd.testing.assert_frame_equal(actual,expected,check_dtype=False,check_exact=True,check_freq=False)
        if record['status']=='FITTED':
            for alpha in [.1,.15,.2]:
                state=0;states=[]
                for a,b in zip(expected.h12,expected.h21,strict=True):
                    previous=state
                    if previous==0:
                        state=1 if a<alpha and b>1-alpha else(-1 if a>1-alpha and b<alpha else 0)
                    elif abs(a-.5)<.1 and abs(b-.5)<.1:state=0
                    states.append(state)
                assert np.array_equal(states,actual[f'state_{int(alpha*100)}']);nstates+=len(states)
        else:assert actual[['state_10','state_15','state_20']].eq(0).all().all()
        records.append({'asof':str(asof),'status':record['status'],'selected':selected,'decision_hours':len(expected)})
    causal=[]
    for edge in ['2025-01-10T00:00Z','2025-05-16T00:00Z','2026-08-28T00:00Z']:
        cutoff=pd.Timestamp(edge);before=prices.index<cutoff;changed=prices.copy();changed.loc[~before]*=3
        original=allrecords[cutoff]
        for mode,panel in [('prefix',prices.loc[before]),('future_perturbed',changed)]:
            nom=form.nominate(panel,cutoff,pd.Timestamp('2025-05-21T09:00Z'),ref);assert nom==original['nomination']
            fitted,model=form.fit_week(panel,nom,pv,ref)
            if fitted['status']=='FITTED':
                old=original['copula'];new=fitted['copula'];assert old['selection_index']==new['selection_index']
                assert max(abs(a['training_loglik']-b['training_loglik'])for a,b in zip(old['all_candidates'],new['all_candidates'],strict=True))<1e-7
            else:assert original['status']=='INSUFFICIENT_STATIONARITY'
            causal.append({'edge':edge,'mode':mode,'actual_nomination_and_estimator_refit':True,'status':fitted['status']})
    assert ncop==61*39 and nmarg==61*6
    eos={str(alpha):int(((forecasts.symbol1.eq('EOS')|forecasts.symbol2.eq('EOS'))&forecasts[f'state_{alpha}'].ne(0)).sum())for alpha in [10,15,20]};assert eos==capsule['EOS_nonzero_hourly_decisions']
    # An active EOS leg is a demonstrated input requirement, not an excuse to
    # delete the source member or pretend the unresolved specification is zero.
    assert all(v>0 for v in eos.values())
    result={'passed_within_model_scope':True,'source_universe_members':20,'native_weekly_records':88,'fitted_weeks':61,'insufficient_stationarity_weeks':27,'coefficient_and_Kendall_checks':ncoeff,'maximum_OLS_difference':maxbeta,'maximum_independent_tau_difference':maxtau,'copula_likelihoods_rechecked':ncop,'marginal_likelihoods_rechecked':nmarg,'maximum_loglik_difference':maxlik,'full_hourly_decisions':len(forecasts),'source_state_cells_independently_replayed':nstates,'causal_refits':causal,'EOS_nonzero_hourly_decisions':eos,'EOS_specification_still_required':True,'actual_native_funded_accounts':0,'new_accepted_alpha':0,'returns':'UNKNOWN','goal_achieved':False,'goal_status':'active','records':records}
    (HERE/'model_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k not in ['records','causal_refits']},indent=2))
if __name__=='__main__':main()
