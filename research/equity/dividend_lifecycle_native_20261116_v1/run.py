"""Serial, offline synthetic native lifecycle checks, not economic samples."""
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def main():
    frozen = json.loads((HERE / 'implementation_freeze.json').read_text())
    receipts = []
    for market in ['XNYS', 'XHKG']:
        for case in ['none', 'pending', 'paid', 'delayed', 'future1', 'future999']:
            for row in frozen:
                assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256']
            assert shutil.disk_usage(ROOT).free >= 2 * 1024**3
            name = market + '_' + case
            command = [sys.executable, '-B', str(HERE / 'native_probe.py'), '--market', market,
                       '--attempt', name, '--dividend-case', case]
            with (HERE / (name + '.log')).open('xb') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
                receipt = dict(case=name, pid=child.pid, command=command,
                               started_at=datetime.now(UTC).isoformat())
                try:
                    returncode = child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    raise
                receipt.update(returncode=returncode, finished_at=datetime.now(UTC).isoformat())
                receipts.append(receipt)
                (HERE / 'execution_receipt.json').write_text(json.dumps(receipts, indent=2) + '\n')
                assert returncode == 0, name
            print(name, 'complete', flush=True)


if __name__ == '__main__':
    main()
