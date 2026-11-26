"""Serial guarded native accounts. Do not restart failed economic attempts."""
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC,datetime
from pathlib import Path

from register import sha,save

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def main():
    path=HERE/'plan.json';plan=json.loads(path.read_text());out=Path(plan['output_root'])
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    assert 'passed' in (HERE/'tests.log').read_text() and 'failed' not in (HERE/'tests.log').read_text()
    env={k:os.environ[k] for k in ['PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR'] if k in os.environ}
    env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    for cost in plan['total_cost_bps_per_side']:
        for candidate in plan['candidates']:
            name=f'{candidate["name"]}_{cost}bps';directory=out/name
            if directory.exists():
                receipt=json.loads((directory/'process_receipt.json').read_text())
                if receipt['returncode']!=0 or sha(directory/'result.json')!=receipt['result_sha256']:
                    raise RuntimeError('Existing failed/incomplete attempt retained: '+name)
                continue
            if shutil.disk_usage(ROOT).free<2*1024**3:raise RuntimeError('Under2GiB free beforeworker')
            command=[sys.executable,'-B','-m',plan['runner_module'],'--plan',str(path),'--candidate',candidate['name'],
                     '--cost-bps',str(cost),'--output',str(directory)]
            start=datetime.now(UTC).isoformat()
            with (out/f'{name}.log').open('xb') as log:
                child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                save(out/'active_process.json',dict(pid=child.pid,command=command,account=name,started_at=start))
                code=child.wait()
            receipt=dict(pid=child.pid,command=command,started_at=start,finished_at=datetime.now(UTC).isoformat(),returncode=code,
                         result_sha256=sha(directory/'result.json') if (directory/'result.json').exists() else None)
            if directory.exists():save(directory/'process_receipt.json',receipt)
            print(json.dumps(dict(account=name,**receipt)),flush=True)
            if code:raise RuntimeError('Native attempt failed; no automatic retry')
            cache=directory/'lossless_cache'
            inventory={str(p.relative_to(cache)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in cache.rglob('*') if p.is_file()}
            save(directory/'generated_cache_inventory.json',inventory)
            shutil.rmtree(cache)
    save(HERE/'execution_complete.json',dict(accounts=12,finished_at=datetime.now(UTC).isoformat(),orders_enabled=False))


if __name__=='__main__':main()
