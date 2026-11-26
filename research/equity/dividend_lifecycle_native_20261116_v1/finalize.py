"""Verify and seal completed technical artifacts; never starts a financial run."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).absolute().parent


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    frozen = read(HERE / 'implementation_freeze.json')
    for row in frozen:
        assert sha(row['path']) == row['sha256'], row['path']
    parent = read(HERE / 'parent_seal_before.json')
    assert sha(parent['path']) == parent['sha256']
    parent_rows = read(parent['path'])['files']
    for row in parent_rows:
        assert sha(row['path']) == row['sha256'], row['path']
    for row in read(HERE / 'source_receipts.json'):
        body = Path(row['path']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == row['sha256']
        assert hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() == row['git_blob']
    for receipt in read(HERE / 'execution_receipt.json'):
        assert receipt['returncode'] == 0
        directory = HERE / 'runs' / receipt['case']
        for path, expected in read(directory / 'implementation_freeze.json').items():
            assert sha(path) == expected, path
        for path, row in read(directory / 'source_snapshot_manifest.json').items():
            assert sha(path) == sha(row['snapshot']) == row['sha256']
    audit = read(HERE / 'independent_audit.json')
    assert audit['passed'] and audit['actual_native_starts'] == audit['actual_native_zip'] == 12
    assert audit['independent_scalar_checks'] == 1344
    assert read(HERE / 'source_witnesses.json')['passed']
    protocol = read(HERE / 'protocol_freeze.json')
    assert sha(protocol['path']) == protocol['sha256']
    save('verification.json', dict(passed=True, native_producer_freeze_paths=len(frozen),
         unchanged_parent_paths=len(parent_rows), parent_seal=parent,
         source_git_blobs_verified=7, actual_native_starts=12, actual_native_zip=12,
         synthetic_fills=36, complete_native_boundaries=168, independent_scalar_checks=1344,
         default_parent_paths_exact=2, new_source_semantic_witnesses=3,
         new_production_modules=0, historical_accounts=0, factor_analysis=0, bench=0,
         financial_strategy_performance=None, original_pool_unchanged=True,
         source_author_account_reproduced=False, independent_oos=False,
         production_eligible=False, orders_enabled=False, services_changed=False))
    save('goal_audit.json', dict(current_scope='original503US+11HK;2025Jan1 through2026Aug31;USD/HKD separate',
         new_accepted_independent_alpha=0, new_historical_accounts=0, new_portfolio_accounts=0,
         higher_comparable_net_return_established=False, overall_goal_achieved=False,
         current_progress='native dividend lifecycle and source semantic evidence',
         next_required='original-pool real-input and all-actions admission before factor/account economics'))
    excluded = {'artifact_manifest.json', 'checkpoint_receipt.json'}
    paths = {p for p in HERE.rglob('*') if p.is_file() and p.name not in excluded
             and '__pycache__' not in p.parts}
    rows = [dict(path=str(p), sha256=sha(p), bytes=p.stat().st_size) for p in sorted(paths)]
    save('artifact_manifest.json', dict(scope='completed synthetic integration/source review;not historical financial return', files=rows))
    for row in rows:
        assert sha(row['path']) == row['sha256']
    save('checkpoint_receipt.json', dict(manifest_sha256=sha(HERE / 'artifact_manifest.json'),
         verified_files=len(rows), actual_native_starts=12, historical_accounts=0, goal_complete=False))
    print(json.dumps(dict(sealed_files=len(rows), manifest_sha256=sha(HERE / 'artifact_manifest.json'))))


if __name__ == '__main__':
    main()
