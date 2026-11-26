"""Continue only independent admitted frozen cohorts; never rerun a failed wallet."""
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save,sha

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def main():
    plan_path=HERE/'plan.json';plan=json.loads(plan_path.read_text());out=Path(plan['output_root'])
    review=json.loads((HERE/'native_limit_review.json').read_text())
    allowed={r['candidate']:r['cohort_admitted']for r in review['rows']}
    env={k:os.environ[k]for k in ('PATH','HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR')if k in os.environ}
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    states=[]
    for c in plan['candidates']:
        name=c['name']+f"_{c['cost_bps']}bps";directory=out/name
        if directory.exists():
            receipt=json.loads((directory/'process_receipt.json').read_text())
            if receipt['returncode']==0:
                assert sha(directory/'result.json')==receipt['result_sha256']
                state='completed'
            else:state='failed_preserved_not_retried'
        elif not allowed[c['name']]:state='not_started_native_pair_cohort_inadmissible'
        else:
            if shutil.disk_usage(ROOT).free<2*1024**3:raise RuntimeError('Below2GiB floor')
            command=[sys.executable,'-B','-m',plan['runner_module'],'--plan',str(plan_path),
                '--candidate',c['name'],'--cost-bps',str(c['cost_bps']),'--output',str(directory)]
            started=datetime.now(UTC).isoformat()
            with (out/(name+'.log')).open('xb')as log:
                child=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                save(out/'active_process_continuation.json',dict(pid=child.pid,account=name,command=command,started_at=started))
                code=child.wait()
            receipt=dict(pid=child.pid,command=command,started_at=started,
                finished_at=datetime.now(UTC).isoformat(),returncode=code,
                result_sha256=sha(directory/'result.json')if(directory/'result.json').exists()else None)
            save(directory/'process_receipt.json',receipt)
            print(json.dumps(dict(account=name,**receipt)),flush=True)
            if code:raise RuntimeError('New native failure retained,stop for review')
            cache=directory/'lossless_cache'
            save(directory/'generated_cache_inventory.json',{str(p.relative_to(cache)):
                dict(bytes=p.stat().st_size,sha256=sha(p))for p in cache.rglob('*')if p.is_file()})
            shutil.rmtree(cache);state='completed'
        states.append(dict(candidate=c['name'],directory=str(directory),state=state,
            cohort_admitted=allowed[c['name']]))
    save(HERE/'execution_review_complete.json',dict(planned=24,states=states,
        completed_native_wallets=sum(r['state']=='completed'for r in states),
        failed_preserved=sum(r['state']=='failed_preserved_not_retried'for r in states),
        not_started=sum(r['state'].startswith('not_started')for r in states),
        completed_review=True,financial_matrix_complete=False,finished_at=datetime.now(UTC).isoformat(),orders_enabled=False))


if __name__=='__main__':main()
