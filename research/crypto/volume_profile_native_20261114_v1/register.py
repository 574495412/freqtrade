"""Freeze actual native account plans; registration itself is not a backtest."""
from pathlib import Path
import json,hashlib
from .prepare import HERE,ROOT,OUT,ref,save,sha

def main():
    assert not(HERE/'plan.json').exists()
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    assert '13 passed'in(HERE/'tests_final.log').read_text()
    base=ROOT/'research/crypto/dual_thrust_native_20261114_v1';plan=json.loads((base/'plan.json').read_text())
    plan.update(schema='freqtrade.native.volume-profile.v1',datadir=str(OUT/'candles'),output_root=str(OUT),data_receipts=ref(OUT/'data_receipts.json'),candidates=[],allowed_leverage=3,runner_module='research.crypto.volume_profile_native_20261114_v1.worker')
    plan['marks']={'BTC/USDT:USDT':ref(OUT/'candles/futures/BTC_USDT_USDT-1h-mark.feather')}
    sources=[HERE/'ProfilePortfolio.py',HERE/'ProfileRules.py',HERE/'worker.py',HERE/'bootstrap.py',ROOT/'freqtrade/optimize/volume_profile.py',HERE/'protocol.json',HERE/'protocol_freeze.json',HERE/'preflight.json',HERE/'tests_final.log']
    receipts=ROOT/'research/crypto/mechanism_source_intake_20261114_v1/source_receipts.json';sources.append(receipts)
    for record in json.loads(receipts.read_text()):
        if '/profile/'in record['path']:
            assert sha(record['path'])==record['sha256'];sources.append(Path(record['path']))
    plan['additional_source_seals']=[ref(p)for p in sources]
    for strategy in ['ProfileBreakout','ProfileFade','ProfileNoVolume']:
        for cash in [1000,500]:
            cfg=json.loads((base/'SourceDualBTC_1000.json').read_text());cfg.update(strategy=strategy,timeframe='15m',startup_candle_count=192,dry_run_wallet=cash,custom_price_max_distance_ratio=1.0)
            name=f'{strategy}_{cash}';path=HERE/f'{name}.json';save(path,cfg)
            plan['candidates'].append({'name':name,'strategy':strategy,'capital':cash,'pairs':['BTC/USDT:USDT'],'config':ref(path),'source_files':{f:ref(HERE/f)for f in ['ProfilePortfolio.py','ProfileRules.py']}})
    save(HERE/'plan.json',plan)
    save(HERE/'prelaunch_manifest.json',{str(p):sha(p)for p in list(HERE.glob('*.py'))+[ROOT/'freqtrade/optimize/volume_profile.py']})
    print('Frozen12 native financial accounts; source/calendar/risk/cost rules unchanged')
if __name__=='__main__':main()
