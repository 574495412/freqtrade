"""Run frozen X7 official lookahead diagnostic, after X8 and without credentials."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import psutil

ROOT=Path(__file__).resolve().parents[2]
CZ=Path('/Users/chrise/GitHub/CZ-Agent')
PLAN=ROOT/'research/crypto/strategy_comparison_wide_20261105/lookahead-diagnostic-plan-x7.json'
OUT=ROOT/'user_data/crypto_quant/nfi_x7_lookahead_check_20261105_v001'

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for b in iter(lambda:stream.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def check_no_other_backtest():
    old=json.loads((ROOT/'user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/process.json').read_text())
    if old['state']!='COMPLETED':raise ValueError('X8 diagnostic not completed')
    for proc in psutil.process_iter(['pid','cmdline']):
        try:
            args=proc.info['cmdline'] or []
            if any(x in args for x in ('lookahead-analysis',)) or any(
                token in arg for arg in args for token in ('run_spot_comparison.py','run_wide_comparison.py')):
                raise ValueError(f"Another financial replay or diagnostic is running (pid={proc.info['pid']})")
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue

def check_inputs(plan):
    if sha(Path(__file__))!=plan['runner_sha256']:
        raise ValueError('Diagnostic runner changed')
    manifest_path=CZ/'vendor/FREQTRADE_SOURCE.json'
    if sha(manifest_path)!=plan['engine_manifest_sha256']:
        raise ValueError('Engine manifest changed')
    manifest=json.loads(manifest_path.read_text());engine=CZ/'vendor/freqtrade'
    if manifest['commit']!=plan['engine_commit'] or len(manifest['files'])!=595:
        raise ValueError('Wrong official engine manifest')
    for name,digest in manifest['files'].items():
        if sha(engine/name)!=digest:raise ValueError('Engine source changed: '+name)
    expected={name for name in manifest['files'] if name.endswith('.py')}
    if {str(p.relative_to(engine)) for p in engine.rglob('*.py')}!=expected:
        raise ValueError('Extra engine source')
    for key in ('strategy_source','upstream_snapshot','config','data_receipts','x8_result'):
        record=plan[key]
        if sha(Path(record['path']))!=record['sha256']:raise ValueError('Pinned input changed: '+key)
    receipts=json.loads(Path(plan['data_receipts']['path']).read_text())
    selected={name:d for name,d in receipts.items() if any(name.startswith('futures/'+s+'_USDT_USDT-') for s in ('BTC','ETH','ADA'))}
    if len(selected)!=21:raise ValueError('Unexpected diagnostic input coverage')
    for name,digest in selected.items():
        if sha(Path(plan['datadir'])/name)!=digest:raise ValueError('Data changed: '+name)
    config=json.loads(Path(plan['config']['path']).read_text())
    if config.get('dry_run') is not True or config.get('trading_mode')!='futures' or config.get('margin_mode')!='isolated':
        raise ValueError('Unexpected diagnostic config')
    if any(config.get('exchange',{}).get(k) for k in ('key','secret','password')):
        raise ValueError('Diagnostic config contains credentials')
    if config.get('api_server',{}).get('enabled') or config.get('telegram',{}).get('enabled'):
        raise ValueError('External messaging or API configured')
    return len(manifest['files']),len(selected)

def main():
    plan=json.loads(PLAN.read_text())
    check_no_other_backtest()
    engine_count,data_count=check_inputs(plan)
    OUT.mkdir(parents=True,exist_ok=False)
    (OUT/'registered_plan.json').write_bytes(PLAN.read_bytes())
    strategy=Path(plan['strategy_source']['path']);csv=OUT/'lookahead-result.csv'
    cmd=[str(CZ/'.venv-freqtrade/bin/freqtrade'),'lookahead-analysis','--config',plan['config']['path'],
         '--strategy-path',str(strategy.parent),'--strategy',plan['strategy'],'--datadir',plan['datadir'],
         '--userdir',str(OUT),'--timerange',plan['timerange'],'--pairs',*plan['pairs'],
         '--minimum-trade-amount',str(plan['minimum_trade_amount']),
         '--targeted-trade-amount',str(plan['targeted_trade_amount']),
         '--lookahead-analysis-exportfilename',str(csv)]
    # Explicit allowlist: no API keys, credential variables, proxy credentials,
    # messaging secrets or FREQTRADE__ overrides are inherited.
    env={key:os.environ[key] for key in ('PATH','LANG','LC_ALL','TZ','TMPDIR','SSL_CERT_FILE') if key in os.environ}
    env['PYTHONDONTWRITEBYTECODE']='1'
    started=time.monotonic();log=OUT/'lookahead.log'
    with log.open('x') as stream:
        process=subprocess.Popen(cmd,cwd=CZ,stdout=stream,stderr=subprocess.STDOUT,env=env)
        status={'pid':process.pid,'command':cmd,'state':'RUNNING','credentials_passed':False,
                'environment_allowlist':sorted(env),'official_synthetic_diagnostic':True,'orders_enabled':False}
        (OUT/'process.json').write_text(json.dumps(status,indent=2)+'\n');print(json.dumps(status),flush=True)
        peak=0;reason=None
        while process.poll() is None:
            try:rss=psutil.Process(process.pid).memory_info().rss
            except psutil.NoSuchProcess:break
            peak=max(peak,rss)
            if rss>plan['memory_guard_bytes'] or time.monotonic()-started>plan['wall_guard_seconds']:
                reason='rss_guard' if rss>plan['memory_guard_bytes'] else 'wall_guard';process.terminate();break
            time.sleep(2)
        try:code=process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();code=process.wait()
    check_inputs(plan)
    status.update(state='RESOURCE_ABORTED' if reason else ('COMPLETED' if code==0 else 'FAILED'),
                  exit_code=code,reason=reason,peak_rss_bytes=peak,elapsed_seconds=time.monotonic()-started,
                  plan_sha256=sha(PLAN),runner_sha256=sha(Path(__file__)),log_sha256=sha(log),
                  official_engine_files_verified=engine_count,data_files_verified=data_count,
                  result_csv_sha256=sha(csv) if csv.exists() else None,
                  source_package_has_bytecode=(strategy.parent/'__pycache__').exists())
    (OUT/'process.json').write_text(json.dumps(status,indent=2)+'\n');print(json.dumps(status),flush=True)
    if code:raise SystemExit(code)

if __name__=='__main__':main()
