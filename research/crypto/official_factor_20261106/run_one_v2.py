"""Bounded subprocess orchestration of the official CLI; no financial model."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
RUNTIME=ROOT/'user_data/crypto_quant/official_factor_20261106'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def now():return datetime.now(timezone.utc).isoformat()
def write(path,obj):
    with path.open('x') as f:json.dump(obj,f,indent=2,ensure_ascii=False);f.write('\n')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--attempt',type=int,choices=(0,1,2),default=0)
    args=parser.parse_args()
    plan=json.loads((HERE/'plan.json').read_text())
    row=next(r for r in plan['runs'] if r['id']==args.run_id)
    with (RUNTIME/'financial_worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        changed=subprocess.check_output(['git','diff','--name-only',plan['engine_commit'],'--','freqtrade'],cwd=ROOT,text=True).splitlines()
        if any(not p.startswith('freqtrade/research/') for p in changed):
            raise ValueError('official core source differs from pinned commit')
        for rel,expected in json.loads((HERE/'freeze.json').read_text())['files'].items():
            if sha(ROOT/rel)!=expected:raise ValueError('changed registered source/config: '+rel)
        data_receipt=ROOT/row['data_receipt']['path']
        if sha(data_receipt)!=row['data_receipt']['sha256']:raise ValueError('changed data receipt')
        hashes=json.loads(data_receipt.read_text())
        for rel,expected in hashes.items():
            if sha(ROOT/row['datadir']/rel)!=expected:raise ValueError('changed market data: '+rel)
        active=json.loads((RUNTIME/(row['market']+'_active_pairs_retry1.json')).read_text())
        missing=set(plan['markets'][row['market']]['pairs'])-set(active)
        if missing:raise ValueError('official current market list missing frozen pairs: '+str(sorted(missing)))
        fix=json.loads((HERE/'technical_fix1.json').read_text())
        for rel,expected in fix['files'].items():
            if sha(ROOT/rel)!=expected:raise ValueError('technical fix hash changed: '+rel)
        config=ROOT/row['config']
        if row['market']=='futures':config=ROOT/fix['configs'][row['cost']]['path']
        output=RUNTIME/'accounts'/(row['id']+(f'__retry{args.attempt}' if args.attempt else ''))
        output.mkdir(parents=True,exist_ok=False)
        command=[sys.executable,'-B','-m','freqtrade','backtesting','--config',str(config),
                 '--strategy',row['strategy'],'--strategy-path',str(HERE/'sources'),
                 '--userdir',str(RUNTIME/'native_userdir'),'--datadir',str(ROOT/row['datadir']),
                 '--timerange',row['timerange'],'--cache','none','--export','trades',
                 '--backtest-directory',str(output),'--breakdown','month','--no-color']
        if row['timeframe_detail']:command+=['--timeframe-detail',row['timeframe_detail']]
        # Explicit config, no FREQTRADE__ overrides or credential environment variables.
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR') if k in os.environ}
        env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1')
        start={'run_id':row['id'],'attempt':args.attempt,'technical_fix_sha256':sha(HERE/'technical_fix1.json'),'started_at_utc':now(),'command':command,
               'freeze_sha256':sha(HERE/'freeze.json'),'runner_sha256':sha(Path(__file__)),
               'engine_core_commit':plan['engine_commit'],'orders_enabled':False,
               'native_assumptions':True,'custom_engine_or_RD1':False,'data_files_verified':len(hashes)}
        write(output/'start.json',start)
        start_clock=time.monotonic();peak=0;stop_reason=None
        with (output/'native.log').open('xb') as log:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            write(output/'process.json',{'pid':child.pid,'started_at_utc':now(),'command':command})
            proc=psutil.Process(child.pid)
            while child.poll() is None:
                try:peak=max(peak,proc.memory_info().rss)
                except psutil.NoSuchProcess:pass
                if peak>plan['budget']['max_rss_bytes']:stop_reason='registered_memory_limit'
                if time.monotonic()-start_clock>plan['budget']['wall_seconds_per_run']:stop_reason='registered_time_limit'
                if stop_reason:
                    child.terminate()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
                    break
                time.sleep(.5)
            code=child.wait()
        files=[p for p in output.iterdir() if p.is_file()]
        archives=list(output.glob('*.zip'))
        produced=len(archives)==1
        effective_code=code if code else (1 if stop_reason or not produced else 0)
        result=dict(start,finished_at_utc=now(),returncode=code,stop_reason=stop_reason,
                    native_result_produced=produced,effective_exit_code=effective_code,elapsed_seconds=time.monotonic()-start_clock,peak_rss_bytes=peak,
                    artifacts={p.name:sha(p) for p in files},economic_qualified=False)
        write(output/'receipt.json',result)
        with (RUNTIME/'execution_ledger.jsonl').open('a') as f:
            f.write(json.dumps({'run_id':row['id'],'returncode':code,'receipt':str((output/'receipt.json').relative_to(ROOT))})+'\n')
            f.flush();os.fsync(f.fileno())
        print(json.dumps({'run_id':row['id'],'returncode':code,'stop_reason':stop_reason,'output':str(output)}),flush=True)
        return effective_code


if __name__=='__main__':raise SystemExit(main())
