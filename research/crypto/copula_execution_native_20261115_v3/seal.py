"""Seal this bounded preparation checkpoint, without redefining the full goal."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_execution_native_20261115_v3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    assert not (HERE / 'artifact_manifest.json').exists()
    check = json.loads((HERE / 'verification.json').read_text())
    assert check['passed_within_preparation_scope'] and not check['goal_achieved']
    files = []
    for root in [HERE, OUT]:
        for p in sorted(root.rglob('*')):
            if not p.is_file() or any(s in {'.git', '.pytest_cache', '__pycache__'} for s in p.relative_to(root).parts): continue
            if p.name in {'artifact_manifest.json', 'checkpoint_receipt.json', 'seal.log'}: continue
            files.append(dict(path=str(p), sha256=sha(p), bytes=p.stat().st_size))
    owner = ROOT / 'freqtrade/data/copula_execution.py'
    files.append(dict(path=str(owner), sha256=sha(owner), bytes=owner.stat().st_size))
    value = dict(sealed_at=datetime.now(UTC).isoformat(), scope='Input and native execution-signal preparation, not complete funded accounts',
                 files=files, paths=len(files), logical_bytes=sum(r['bytes'] for r in files))
    (HERE / 'artifact_manifest.json').write_text(json.dumps(value, indent=2) + '\n')
    for rec in files: assert sha(rec['path']) == rec['sha256']
    receipt = dict(checkpoint_complete=True, objective_complete=False, goal_status='active',
                   paths=len(files), logical_bytes=value['logical_bytes'], manifest_sha256=sha(HERE / 'artifact_manifest.json'),
                   new_native_funded_accounts=0, financial_returns='UNKNOWN', all_manifest_paths_rechecked=True)
    (HERE / 'checkpoint_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('SEALED', len(files), 'paths;', value['logical_bytes'], 'logical bytes;goalactive;0newfundedaccounts')


if __name__ == '__main__':
    main()
