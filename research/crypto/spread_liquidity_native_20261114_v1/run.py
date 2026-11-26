"""Serial bounded native workers; retain failed attempts and exact exit status."""
from datetime import UTC, datetime
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/spread_liquidity_native_20261114_v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, default=HERE / 'plan.json')
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan = json.loads(plan_path.read_text())
    output_root = Path(plan.get('output_root', OUT))
    if not json.loads((HERE / 'preflight.json').read_text())['passed']:
        raise ValueError('Preflight is required')
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', NUMEXPR_MAX_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    for cost in plan['total_cost_bps_per_side']:
        for candidate in plan['candidates']:
            name = f'{candidate["name"]}_{cost}bps'
            output = output_root / name
            if output.exists():
                receipt = json.loads((output / 'process_receipt.json').read_text())
                if receipt['returncode'] != 0 or sha(output / 'result.json') != receipt['result_sha256']:
                    raise ValueError('Preserve incomplete/failed attempt: ' + name)
                continue
            if shutil.disk_usage(ROOT).free < 2 * 1024**3:
                raise RuntimeError('Less than2GiB free before worker')
            command = [sys.executable, '-B', '-m', plan.get('runner_module', 'research.crypto.spread_liquidity_native_20261114_v1.native_runner'),
                       '--plan', str(plan_path), '--candidate', candidate['name'],
                       '--cost-bps', str(cost), '--output', str(output)]
            started = datetime.now(UTC).isoformat()
            with (output_root / f'{name}.log').open('xb') as log:
                child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                save(output_root / 'active_process.json', {'pid': child.pid, 'account': name, 'command': command, 'started_at': started})
                code = child.wait()
            receipt = {'pid': child.pid, 'command': command, 'started_at': started,
                       'finished_at': datetime.now(UTC).isoformat(), 'returncode': code,
                       'result_sha256': sha(output / 'result.json') if (output / 'result.json').exists() else None}
            if output.exists():
                save(output / 'process_receipt.json', receipt)
            print(json.dumps({'account': name, **receipt}), flush=True)
            if code:
                raise RuntimeError('Native worker failed; no automatic economic retry')
            cache = output / 'lossless_cache'
            inventory = {str(p.relative_to(cache)): {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in cache.rglob('*') if p.is_file()}
            save(output / 'generated_cache_inventory.json', inventory)
            shutil.rmtree(cache)
    save(plan_path.parent / 'execution_complete.json', {'accounts': len(plan['candidates']) * len(plan['total_cost_bps_per_side']), 'finished_at': datetime.now(UTC).isoformat(), 'orders_enabled': False})


if __name__ == '__main__':
    main()
