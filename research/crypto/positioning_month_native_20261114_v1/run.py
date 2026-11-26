"""Serial bounded native replay; preserve every completed or failed account."""
import fcntl,json,os,shutil,subprocess,sys
from datetime import UTC,datetime
from pathlib import Path
from .prepare import HERE,ROOT,sha,save
from .dedup_cache import dedup

def main():
    plan_path=HERE/'plan.json';plan=json.loads(plan_path.read_text());out=Path(plan['output_root'])
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    env={k:os.environ[k]for k in ['PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR']if k in os.environ};env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    with(HERE/'financial_worker.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for cost in plan['total_cost_bps_per_side']:
            for c in plan['candidates']:
                key=f'{c["name"]}_{cost}bps';destination=out/key
                if destination.exists():
                    r=json.loads((destination/'process_receipt.json').read_text());assert r['returncode']==0 and sha(destination/'result.json')==r['result_sha256'],'Preserve failed/partial account; no automatic retry';continue
                assert shutil.disk_usage(ROOT).free>=2*1024**3,'Less than2GiBfree'
                command=[sys.executable,'-B','-m',plan['runner_module'],'--plan',str(plan_path),'--candidate',c['name'],'--cost-bps',str(cost),'--output',str(destination)]
                started=datetime.now(UTC).isoformat()
                with(out/f'{key}.log').open('xb')as log:
                    child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT);save(out/'active_process.json',{'pid':child.pid,'account':key,'command':command,'started_utc':started});code=child.wait()
                receipt={'pid':child.pid,'command':command,'started_utc':started,'finished_utc':datetime.now(UTC).isoformat(),'returncode':code,'result_sha256':sha(destination/'result.json')if(destination/'result.json').exists()else None}
                if destination.exists():save(destination/'process_receipt.json',receipt)
                print(json.dumps({'account':key,**receipt}),flush=True)
                if code:raise RuntimeError('Native worker failed; preserve attempt')
                linked=dedup(out)
                if linked:
                    receipt_path=HERE/'cache_dedup_receipts.json'
                    prior=json.loads(receipt_path.read_text())if receipt_path.exists()else[]
                    save(receipt_path,prior+linked)
        save(HERE/'execution_complete.json',{'accounts':12,'finished_utc':datetime.now(UTC).isoformat(),'live_orders':False})
if __name__=='__main__':main()
