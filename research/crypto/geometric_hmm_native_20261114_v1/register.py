"""Seal all source/model/account contracts before native financial outcomes."""
import copy,json
import re
from pathlib import Path
from prepare import HERE,ROOT,OUT,sha,ref,save

def main():
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    tests=(HERE/'tests_final.log').read_text()
    assert re.search(r'\b12 passed\b',tests) and 'FAILED'not in tests
    assert not (HERE/'plan.json').exists()
    old=json.loads((HERE.parent/'volume_profile_native_20261114_v1/plan.json').read_text())
    plan=copy.deepcopy(old)
    plan.update(schema='freqtrade.native.geometric-hmm.v1',output_root=str(OUT),datadir=str(OUT/'candles'),
                pairs=['BTC/USDT:USDT'],data_receipts=ref(OUT/'data_receipts.json'),allowed_leverage=3.,
                runner_module='research.crypto.geometric_hmm_native_20261114_v1.worker',
                runner_sha256=sha(HERE.parent/'tsmom_risk_native_20261114_v1/native_runner.py'))
    plan['marks']={'BTC/USDT:USDT':ref(OUT/'candles/futures/BTC_USDT_USDT-1h-mark.feather')}
    source={name:ref(HERE/name)for name in['GeoHmmPortfolio.py','CausalGeoHMM.py','NativeQuantityPortfolio.py','NativeAllocations.py']}
    base=json.loads((HERE/'SourceGeoHmmBTC_1000.json').read_text())
    candidates=[]
    for strategy in ['SourceGeoHmmBTC','GeoOnlyBTC','HmmOnlyBTC','ConstantGrossBTC']:
        for capital in [1000,500]:
            name=f'{strategy}_{capital}';cfg=copy.deepcopy(base);cfg.update(strategy=strategy,dry_run_wallet=capital)
            save(HERE/(name+'.json'),cfg)
            candidates.append({'name':name,'strategy':strategy,'capital':capital,'pairs':plan['pairs'],
                               'config':ref(HERE/(name+'.json')),'source_files':source})
    plan['candidates']=candidates
    files=[HERE/name for name in ['worker.py','prepare.py','register.py','protocol.json','preflight.json','test_contract.py','tests_final.log','run.py']]
    files += [p for p in(HERE/'source').rglob('*')if p.is_file()and'__pycache__'not in p.parts]
    files += [OUT/'model_capsule.json',OUT/'native_model_features.parquet']+list((OUT/'models').glob('*.npz'))
    files += list(OUT.glob('preflight_*.parquet'))
    plan['additional_source_seals']=[ref(p)for p in files]
    plan['registered_at']=__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat()
    plan['frozen_combinations']=json.loads((HERE/'protocol.json').read_text())['combinations']
    save(HERE/'plan.json',plan)
    print('Frozen16accounts; two matched costs, actual1000/500wallets')
if __name__=='__main__':main()
