"""Freeze actual native accounts, with original strategy bytes and matched data."""
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]
PARENT=HERE.with_name('trendcore_source_native_20261117_v1')


def record(p):
    p=Path(p);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def main():
    assert not(HERE/'plan.json').exists()
    plan=json.loads((PARENT/'plan.json').read_text());control=plan['candidates'][0]
    out=ROOT/'user_data/crypto_quant'/HERE.name;(out/'candles').mkdir(parents=True,exist_ok=True)
    receipts=json.loads((PARENT/'data_receipts.json').read_text());links=[]
    for name,digest in receipts.items():
        src=Path(plan['datadir'])/name;dst=out/'candles'/name
        assert record(src)['sha256']==digest
        if not dst.exists():dst.hardlink_to(src)
        assert src.stat().st_ino==dst.stat().st_ino and record(dst)['sha256']==digest
        links.append(dict(source=str(src),target=str(dst),sha256=digest))
    (HERE/'data_receipts.json').write_text(json.dumps(receipts,indent=2)+'\n')
    (HERE/'input_receipts.json').write_text(json.dumps(dict(hardlinks=links,source_code_unchanged=True,financial_starts=0),indent=2)+'\n')
    plan.update(registered_at=datetime.now(UTC).isoformat(),schema='source-simple-meanreversion-native-v1',output_root=str(out),datadir=str(out/'candles'),runner_module='research.crypto.'+HERE.name+'.native_runner',runner_sha256=record(HERE/'native_runner.py')['sha256'],data_receipts=record(HERE/'data_receipts.json'))
    plan['candidates']=[]
    source=HERE/'source/SimpleMeanReversion.py';source_files={'SimpleMeanReversion.py':record(source)}
    for capital in [1000,500]:
        for scenario,cost in [('conservative',20),('lower',13)]:
            name=f'reversion_{capital}_{scenario}';config=json.loads(Path(control['config']['path']).read_text())
            for key in ['research_trendcore_nooi','signal_prices','minimal_roi','stoploss','trailing_stop','position_adjustment_enable','max_entry_position_adjustment','use_exit_signal']:
                config.pop(key,None)
            config.update(strategy='SimpleMeanReversion',dry_run_wallet=capital,fee=cost/10000,bot_name=name)
            config_path=HERE/(name+'.json');config_path.write_text(json.dumps(config,indent=2)+'\n')
            row=copy.deepcopy(control);row.update(name=name,strategy='SimpleMeanReversion',capital=capital,scenario=scenario,cost_bps=cost,config=record(config_path),source_files=source_files);plan['candidates'].append(row)
    extra=[HERE/n for n in ['protocol.json','source_receipts.json','input_receipts.json','preflight.py','run.py','CaptureRules.py','prepare.py']]
    extra+=[Path(r['local_path'])for r in json.loads((HERE/'source_receipts.json').read_text())['records']]
    extra+=[ROOT/'research/crypto/band_trend_native_20261113_v1/observe_clock.py',ROOT/'research/crypto/skew_vol_native_20261113_v1/clock_fix.py']
    plan['additional_source_seals']=[record(p)for p in extra]
    (HERE/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    files=[HERE/'plan.json',HERE/'native_runner.py',HERE/'audit_accounts.py',*extra]
    (HERE/'implementation_freeze.json').write_text(json.dumps({str(p):record(p)['sha256']for p in files},indent=2)+'\n')
    print('4nativewalletsregistered;originalsourcebytes;nofinancialstarts')


if __name__=='__main__':main()
