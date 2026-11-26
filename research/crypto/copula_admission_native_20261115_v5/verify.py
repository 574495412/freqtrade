"""Seal actual native fixture execution; never admit a historical account."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_admission_native_20261115_v5'
PAPER = HERE.parent / 'adaptive_overlay_source_native_20261115_v1'
ENGINE = ROOT.parent / 'CZ-Agent/vendor/freqtrade'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def seal(directory, extra=()):
    paths = list(directory.rglob('*')) + list(extra)
    files = sorted({p for p in paths if p.is_file() and '__pycache__' not in p.parts
                    and p.name not in {'artifact_manifest.json', 'checkpoint_receipt.json', 'verify.log'}})
    save(directory / 'artifact_manifest.json', {'sealed_at': datetime.now(UTC).isoformat(),
         'files': [{'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in files]})
    return len(files), sha(directory / 'artifact_manifest.json')


def main():
    native = read(ENGINE.parent / 'FREQTRADE_SOURCE.json')
    assert native['commit'] == 'c051e9065c5b677f03817dd1f83cb980af1ed684'
    for path, digest in native['files'].items():
        assert sha(ENGINE / path) == digest
    assert {str(p.relative_to(ENGINE)) for p in ENGINE.rglob('*.py')} == {p for p in native['files'] if p.endswith('.py')}
    protocol = read(HERE / 'protocol.json')
    for field in ['parent_manifest', 'inputs', 'observation', 'fixture_protocol']:
        row = protocol[field]
        assert sha(row['path']) == row['sha256']
    parent_files = read(protocol['parent_manifest']['path'])['files']
    assert len(parent_files) == 334
    for row in parent_files:
        assert sha(row['path']) == row['sha256']
    audit = read(HERE / 'audit.json')
    assert audit['passed'] and (audit['complete_synthetic_accounts'], audit['correctly_aborted_accounts']) == (4, 2)
    assert audit['source20_funded_accounts'] == 0
    for row in read(HERE / 'execution_receipt.json'):
        assert row['returncode'] == (42 if row['case'] in {'one_leg_rejected', 'missing_funding_exposed'} else 0)
    for directory in (OUT / 'runs').glob('*/native2'):
        for path, record in read(directory / 'implementation_freeze.json').items():
            assert sha(path) == record['sha256']
            assert sha(record['snapshot']) == record['sha256']
    assert '69 passed' in (HERE / 'tests_final.log').read_text()
    assert (HERE / 'lint_production_final.log').read_text().strip() == 'All checks passed!'
    receipt = read(PAPER / 'paper_receipt.json')
    assert sha(receipt['path']) == receipt['sha256']
    source = read(PAPER / 'source_admission.json')
    assert source['passed'] and not source['implementation_admitted'] and source['native_accounts'] == 0
    save(HERE / 'verification.json', dict(passed=True, native_files=len(native['files']),
        parent_paths=len(parent_files), checks=69, new_checks=25, inherited_checks=44,
        complete_synthetic_accounts=4, aborted_synthetic_accounts=2, control_fills=60,
        control_hourly_points=200, preserved_native_phases=2396,
        source20_funded_accounts=0, equity_accounts=0, combinations=0, objective_achieved=False))
    paper_count, paper_digest = seal(PAPER)
    save(PAPER / 'checkpoint_receipt.json', dict(verified=True, bound_paths=paper_count,
        manifest_sha256=paper_digest, source_admitted=False, financial_accounts=0))
    count, digest = seal(HERE, (OUT / 'runs').rglob('*'))
    save(HERE / 'checkpoint_receipt.json', dict(verified=True, bound_paths=count,
        manifest_sha256=digest, source20_funded_accounts=0, equity_accounts=0,
        objective_achieved=False, progress=True, orders_enabled=False, services_changed=False))
    print('SEALED native run admission', count, digest, 'paper source', paper_count, paper_digest)


if __name__ == '__main__':
    main()
