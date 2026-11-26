"""Serial native workers; isolated loader environment, no automatic retry."""
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC,datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,indent=2)+'\n')


def main():
    plan_path=HERE/'v002/plan.json';plan=json.loads(plan_path.read_text());out=Path(plan['output_root'])
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    env={k:os.environ[k] for k in ['PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR'] if k in os.environ}
    env.update(DYLD_LIBRARY_PATH=str(HERE/'dependencies/sklearn/.dylibs'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    for cost in plan['total_cost_bps_per_side']:
        for candidate in plan['candidates']:
            key=f'{candidate["name"]}_{cost}bps';destination=out/key
            if destination.exists():
                receipt=json.loads((destination/'process_receipt.json').read_text())
                assert receipt['returncode']==0 and sha(destination/'result.json')==receipt['result_sha256'],'Preserve incomplete attempt'
                continue
            assert shutil.disk_usage(ROOT).free>=2*1024**3,'2GiB free guard before worker'
            command=[sys.executable,'-B','-m',plan['runner_module'],'--plan',str(plan_path),'--candidate',candidate['name'],'--cost-bps',str(cost),'--output',str(destination)]
            start=datetime.now(UTC).isoformat()
            with (out/f'{key}.log').open('xb') as stream:
                child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
                save(out/'active_process.json',dict(pid=child.pid,command=command,account=key,started_at=start));code=child.wait()
            receipt=dict(pid=child.pid,command=command,started_at=start,finished_at=datetime.now(UTC).isoformat(),returncode=code,
                         result_sha256=sha(destination/'result.json') if (destination/'result.json').exists() else None)
            if destination.exists():save(destination/'process_receipt.json',receipt)
            print(json.dumps(dict(account=key,**receipt)),flush=True)
            if code:raise RuntimeError('Native worker failed; do not automatically retry finances')
            cache=destination/'lossless_cache'
            save(destination/'generated_cache_inventory.json',{str(p.relative_to(cache)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in cache.rglob('*') if p.is_file()})
            shutil.rmtree(cache)
            from research.crypto.freqai_rf_native_20261114_v1.resource_dedup import dedup
            dedup()
    save(HERE/'v002/execution_complete.json',dict(accounts=16,completed_utc=datetime.now(UTC).isoformat(),orders=False))


if __name__=='__main__':main()
