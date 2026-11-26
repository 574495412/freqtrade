"""Bind native HAR/Naive accounts before economic results."""
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]
PARENT=HERE.with_name('universal_allocation_native_20261116_v1')


def record(p):
    p=Path(p);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def main():
    assert not(HERE/'plan.json').exists()
    features=json.loads((HERE/'feature_receipts.json').read_text());assert features['passed']
    assert '6 passed'in(HERE/'tests.log').read_text()
    plan=json.loads((PARENT/'plan.json').read_text());control=plan['candidates'][0]
    out=ROOT/'user_data/crypto_quant'/HERE.name
    plan.update(registered_at=datetime.now(UTC).isoformat(),schema='har-risk-native-v1',output_root=str(out),datadir=str(out/'candles'),runner_module='research.crypto.'+HERE.name+'.native_runner',runner_sha256=record(HERE/'native_runner.py')['sha256'],data_receipts=record(HERE/'data_receipts.json'))
    paths={r['symbol'].replace('USDT','/USDT'):r['path']for r in features['records']}
    source_files={'HARVolTarget.py':record(HERE/'HARVolTarget.py'),'HARSignals.py':record(ROOT/'freqtrade/data/har_volatility.py'),'NativeAllocations.py':record(HERE/'NativeAllocations.py')}
    plan['candidates']=[]
    for capital in [1000,500]:
        for scenario,cost in [('conservative',20),('lower',13)]:
            for label,strategy in [('har','HARVolTarget'),('naive','NaiveVolTarget')]:
                name=f'{label}_{capital}_{scenario}';config=json.loads(Path(control['config']['path']).read_text());config.pop('research_universal_allocation',None)
                config.update(strategy=strategy,dry_run_wallet=capital,fee=cost/10000,bot_name=name,research_har_volatility=True,daily_feature_paths=paths)
                p=HERE/(name+'.json');p.write_text(json.dumps(config,indent=2)+'\n')
                c=copy.deepcopy(control);c.update(name=name,strategy=strategy,capital=capital,scenario=scenario,cost_bps=cost,config=record(p),source_files=source_files);plan['candidates'].append(c)
    extra=[HERE/n for n in ['protocol.json','source_receipts.json','capture_jobs.json','feature_receipts.json','test_forecasts.py','tests.log','prepare_plan.py','preflight.py','run.py','CaptureRules.py']]
    extra+=[Path(r['path'])for r in features['records']]
    extra+=[Path(r['local_path'])for r in json.loads((HERE/'source_receipts.json').read_text())['records']]
    extra+=[ROOT/'research/crypto/band_trend_native_20261113_v1/observe_clock.py',ROOT/'research/crypto/skew_vol_native_20261113_v1/clock_fix.py']
    plan['additional_source_seals']=[record(p)for p in extra]
    (HERE/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    files=[HERE/'plan.json',HERE/'native_runner.py',HERE/'HARVolTarget.py',HERE/'NativeAllocations.py',ROOT/'freqtrade/data/har_volatility.py',*extra]
    (HERE/'implementation_freeze.json').write_text(json.dumps({str(p):record(p)['sha256']for p in files},indent=2)+'\n')
    print('8nativewalletconfigs;0financialstarts')


if __name__=='__main__':main()
