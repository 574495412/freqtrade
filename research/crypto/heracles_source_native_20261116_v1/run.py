"""One native financial worker,immutable plans,explicit terminal receipts."""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.heracles_source_native_20261116_v1.prepare import record, save


HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    if not json.loads((HERE/'preflight.json').read_text())['passed']:
        raise ValueError('Native input/signal preflight required')
    frozen=json.loads((HERE/'implementation_freeze.json').read_text())
    env={k:os.environ[k] for k in ['PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR'] if k in os.environ}
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    receipts=[]
    for candidate in plan['candidates']:
        for path,digest in frozen.items():
            assert record(path)['sha256']==digest,path
        assert shutil.disk_usage(ROOT).free>=512*1024**2,'Resource floor512MiB'
        name=candidate['name']+f"_{candidate['cost_bps']}bps"
        output=Path(plan['output_root'])/name
        assert not output.exists(),'Keep failed or existing financial evidence'
        command=[sys.executable,'-B','-m',plan['runner_module'],'--plan',str(HERE/'plan.json'),
                 '--candidate',candidate['name'],'--cost-bps',str(candidate['cost_bps']),'--output',str(output)]
        row={'account':name,'command':command,'started_at':datetime.now(UTC).isoformat()}
        with (Path(plan['output_root'])/(name+'.log')).open('xb') as log:
            child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            row['pid']=child.pid
            save(HERE/'active_process.json',row)
            code=child.wait()
        row.update(returncode=code,finished_at=datetime.now(UTC).isoformat(),
                   result_sha256=record(output/'result.json')['sha256'] if (output/'result.json').exists() else None)
        receipts.append(row)
        save(HERE/'execution_receipt.json',receipts)
        if output.exists():
            save(output/'process_receipt.json',row)
        print(name,code,flush=True)
        if code:
            raise RuntimeError('Financial failure retained;no automatic economic retry')
        cache=output/'lossless_cache'
        save(output/'generated_cache_inventory.json',
             {str(p.relative_to(cache)):record(p) for p in cache.rglob('*') if p.is_file()})
        shutil.rmtree(cache)
    save(HERE/'execution_complete.json',{'accounts':len(receipts),'finished_at':datetime.now(UTC).isoformat(),
                                       'orders_enabled':False})


if __name__=='__main__':
    main()
