"""Independent public input / saved-kernel likelihood checks; never report PnL."""
import json,sys,importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from .prepare import HERE,ROOT,OUT,sha,save

def main():
    runtime=json.loads((HERE/'runtime_receipt.json').read_text())
    original=runtime['upstream_source_manifest'];assert sha(original['path'])==original['sha256']
    manifest=json.loads(Path(original['path']).read_text());engine=ROOT.parent/'CZ-Agent/vendor/freqtrade'
    assert len(manifest['files'])==595 and all(sha(engine/n)==digest for n,digest in manifest['files'].items())
    assert {str(p.relative_to(engine))for p in engine.rglob('*.py')}=={n for n in manifest['files']if n.endswith('.py')}
    admission=json.loads((HERE/'input_admission.json').read_text());native=json.loads((HERE/'native_preflight.json').read_text());fit=json.loads((HERE/'fit_probe.json').read_text());status=json.loads((HERE/'status.json').read_text())
    assert admission['passed_for_hourly_prices']and not admission['financial_admission']and native['passed_within_scope']and fit['passed_within_scope']
    assert status['native_funded_accounts']==0 and status['financial_returns']=='UNKNOWN'and not status['goal_achieved']
    for record in admission['archives']:
        assert record['passed']
        for key in ['archive','checksum','prepared']:assert sha(record[key]['path'])==record[key]['sha256']
        assert Path(record['checksum']['path']).read_text().split()[0]==record['archive']['sha256']
    assert len(admission['archives'])==132 and sum(r['terminal_observations_quarantined']for r in admission['archives'])==1
    for row in admission['reused']:
        for key in ['source','target']:assert sha(row[key]['path'])==row[key]['sha256']
    receipts=json.loads(Path(admission['data_receipts']['path']).read_text());assert len(receipts)==20
    for path,digest in receipts.items():assert sha(OUT/'candles'/path)==digest
    sys.path.insert(0,str(HERE/'dependencies'));import pyvinecopulib as pv
    spec=importlib.util.spec_from_file_location('verified_copula_owner',ROOT/'freqtrade/data/copula_reference.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    prices=pd.DataFrame({s:pd.read_feather(OUT/f'candles/futures/{s}_USDT_USDT-1h-futures.feather').set_index('date').close for s in m.SYMBOLS})
    assert len(prices)==15336 and prices.shape[1]==20
    assert int(prices.EOS.isna().sum())==11223 and not prices.drop(columns='EOS').isna().any().any()
    likelihood_checks=[];maximum=0
    for path in sorted(OUT.glob('fit_*.json')):
        record=json.loads(path.read_text());assert record['native_owner_sha256']==sha(ROOT/'freqtrade/data/copula_reference.py')
        asof=pd.Timestamp(record['formation']['asof']);beta,spread,diag=m.formation_spreads(prices,asof,pd.Timestamp(admission['terminal_EOS']));uv=[]
        for marginal in record['marginals']:
            x=spread[marginal['symbol']].to_numpy()
            for candidate in marginal['all_candidates']:
                d=getattr(stats,candidate['family']);l=float(d.logpdf(x,*candidate['parameters']).sum());assert abs(l-candidate['loglik'])<1e-7
            selected=marginal['selected'];uv.append(np.clip(getattr(stats,selected['family']).cdf(x,*selected['parameters']),1e-10,1-1e-10))
        uv=np.asfortranarray(np.column_stack(uv));candidates=record['copula']['all_candidates'];assert [(r['family'],r['rotation'])for r in candidates]==m.family_grid(pv)
        for r in candidates:
            family=pv.BicopFamily.tawn if r['family'].startswith('tawn_')else getattr(pv.BicopFamily,r['family']);model=pv.Bicop(family=family,rotation=r['rotation'],parameters=np.array(r['parameters']))
            if r['family']=='tawn_alpha':assert model.parameters[1,0]==1 and r['free_parameters']==2
            elif r['family']=='tawn_beta':assert model.parameters[0,0]==1 and r['free_parameters']==2
            else:assert r['free_parameters']==int(model.npars)
            l=float(np.log(model.pdf(uv)).sum());error=abs(l-r['training_loglik']);maximum=max(maximum,error);assert error<1e-7
            assert abs(2*r['free_parameters']-2*l-r['aic'])<1e-7
        i=min(range(len(candidates)),key=lambda j:(candidates[j]['aic'],j));assert i==record['copula']['selection_index']and candidates[i]==record['copula']['selected']
        likelihood_checks.append({'path':str(path),'sha256':sha(path),'candidates_rechecked':len(candidates),'training_rows':len(uv)})
    assert len(likelihood_checks)==3 and sum(r['candidates_rechecked']for r in likelihood_checks)==117
    deps=json.loads((HERE/'dependency_manifest.json').read_text())
    for r in deps['files']:assert sha(r['path'])==r['sha256']
    assert sha(HERE/'sources/copula_reference_owner_snapshot.py.txt')==sha(ROOT/'freqtrade/data/copula_reference.py')
    if(HERE/'artifact_manifest.json').exists():
        manifest=json.loads((HERE/'artifact_manifest.json').read_text())
        for r in manifest['files']:assert Path(r['path']).is_file()and sha(r['path'])==r['sha256']
    literal_tawn_margin=.2**.6
    assert abs(literal_tawn_margin-.2)>.1
    save(HERE/'verification.json',{'passed_within_input_and_estimator_scope':True,'source_members':20,'native_price_rows':native['input_rows'],'new_official_archive_checks':132,'reused_native_series':13,'copula_family_rotation_combinations':39,'fitted_candidate_likelihoods_rechecked':117,'maximum_loglik_difference':maximum,'printed_Tawn_Eq24_counterexample':{'alpha':.6,'beta':1.,'u':.2,'literal_C_u_1':literal_tawn_margin,'required_margin':.2,'boundary_rejected':True},'likelihood_checks':likelihood_checks,'actual_native_financial_startup_rejected':native['actual_native_financial_startup_rejected'],'source_selected_trading_pairs':False,'native_funded_accounts':0,'account_returns':'UNKNOWN','new_accepted_alpha':0,'goal_achieved':False,'goal_status':'active'})
    print('VERIFIED20source members,132archives,117candidate likelihoods;0funded accounts;returns UNKNOWN')
if __name__=='__main__':main()
