"""Register frozen native finances only after completed component preflight."""
import hashlib
import json
from importlib.metadata import distributions
from pathlib import Path

from prepare import HERE,OUT,ROOT,config_for,ref,save,sha


def main():
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    assert '7 passed' in (HERE/'tests_final.log').read_text()
    assert not (HERE/'plan.json').exists(),'Preserve registered account contract'
    dependencies={str(p.relative_to(HERE/'dependencies')):sha(p) for p in (HERE/'dependencies').rglob('*') if p.is_file()}
    versions={d.metadata['Name']:d.version for d in distributions(path=[str(HERE/'dependencies')])}
    save(HERE/'dependency_manifest.json',dict(versions=versions,files=dependencies,shared_environment_modified=False,
         openmp='Child-only DYLD_LIBRARY_PATH to the libomp bundled in this sklearn wheel; no system/brew changes',xgboost_role='Native disabled Tensorboard base logger import dependency; no XGBoost learner used'))
    plan=json.loads((HERE.parent/'dual_thrust_native_20261114_v1/plan.json').read_text())
    plan.update(schema='freqtrade.native.freqai-rf.v1',output_root=str(OUT),datadir=str(OUT/'candles'),
                data_receipts=ref(OUT/'data_receipts.json'),candidates=[],runner_module='research.crypto.freqai_rf_native_20261114_v1.worker')
    plan['marks']={'BTC/USDT:USDT':ref(OUT/'candles/futures/BTC_USDT_USDT-1h-mark.feather')}
    plan['additional_source_seals']=[ref(HERE/p) for p in ['protocol.json','dependency_manifest.json','worker.py','RFModels.py','ModelRules.py','FreqAIState.py','preflight.json']]
    for name in ['RollingRF','RollingPrior','SmaPriceBTC','HoldBTC']:
        files=['FreqAIState.py','RFModels.py','ModelRules.py','FreqaiExampleStrategy.py'] if name.startswith('Rolling') else ['DualPortfolio.py','DualRules.py']
        for capital in [1000,500]:
            path=HERE/f'{name}_{capital}.json';save(path,config_for(name,capital))
            plan['candidates'].append(dict(name=f'{name}_{capital}',strategy=name,capital=capital,pairs=plan['pairs'],
                 config=ref(path),source_files={p:ref(HERE/p) for p in files}))
    save(HERE/'plan.json',plan)
    save(HERE/'prelaunch_manifest.json',{str(p):sha(p) for p in HERE.glob('*.py')})
    print('Registered16 frozen native accounts; isolated dependencies',versions)


if __name__=='__main__':main()
