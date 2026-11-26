"""Freeze12actual native accounts and predeclared actual500wallet combination."""
import copy,json,re
from pathlib import Path
from prepare import HERE,ROOT,OUT,sha,ref,save

def main():
    assert not(HERE/'plan.json').exists()
    pre=json.loads((HERE/'preflight.json').read_text());assert pre['passed']
    assert re.search(r'\b16 passed\b',(HERE/'tests_final.log').read_text())
    old=json.loads((HERE.parent/'geometric_hmm_native_20261114_v1/plan.json').read_text());plan=copy.deepcopy(old)
    pairs=['BTC/USDT:USDT','ETH/USDT:USDT','SOL/USDT:USDT']
    plan.update(schema='freqtrade.native.positioning-month.v1',pairs=pairs,output_root=str(OUT),datadir=str(OUT/'candles'),
                data_receipts=ref(OUT/'data_receipts.json'),runner_module='research.crypto.positioning_month_native_20261114_v1.worker')
    plan['marks']={p:ref(OUT/'candles/futures'/f'{p.split("/")[0]}_USDT_USDT-1h-mark.feather')for p in pairs}
    source={n:ref(HERE/n)for n in['PositioningPortfolio.py','Signals.py','NativeQuantityPortfolio.py','NativeAllocations.py']}
    base=json.loads((HERE/'LowCrowdMonth_1000.json').read_text());candidates=[]
    for strategy in ['LowCrowdMonth','LowPositionMonth','ConstantPositioningControl']:
        for capital in [1000,500]:
            name=f'{strategy}_{capital}';config=copy.deepcopy(base);config.update(strategy=strategy,dry_run_wallet=capital)
            save(HERE/(name+'.json'),config);candidates.append({'name':name,'strategy':strategy,'capital':capital,'pairs':pairs,
                'config':ref(HERE/(name+'.json')),'source_files':source})
    plan['candidates']=candidates
    files=[HERE/n for n in['worker.py','run.py','dedup_cache.py','prepare.py','register.py','protocol.json','preflight.json','test_inputs.py','tests_final.log','native_input_binding.json']]
    files += [ROOT/'freqtrade/data/binance_positioning.py',OUT/'raw_receipts.json',OUT/'signal_capsule.json']
    files += list(OUT.glob('daily_*.parquet'))+list(OUT.glob('preflight_*.parquet'))
    files += [p for p in(HERE/'source').rglob('*')if p.is_file()]
    plan['additional_source_seals']=[ref(p)for p in files]
    plan['frozen_combinations']=[json.loads((HERE/'protocol.json').read_text())['combination']]
    plan['registered_at']=__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat()
    save(HERE/'plan.json',plan);print('Frozen12nativeaccounts before any financial outcome')
if __name__=='__main__':main()
