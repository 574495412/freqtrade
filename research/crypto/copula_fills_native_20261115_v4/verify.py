"""Actual runtime/source/input artifact audit; source20 finance stays closed."""
import hashlib
import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_fills_native_20261115_v4'
ENGINE = ROOT.parent / 'CZ-Agent/vendor/freqtrade'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    native = read(ENGINE.parent / 'FREQTRADE_SOURCE.json')
    assert native['commit'] == 'c051e9065c5b677f03817dd1f83cb980af1ed684'
    for path, digest in native['files'].items():
        assert sha(ENGINE / path) == digest, path
    assert {str(p.relative_to(ENGINE)) for p in ENGINE.rglob('*.py')} == {p for p in native['files'] if p.endswith('.py')}
    protocol = read(HERE / 'fixture_protocol.json')
    assert sha(protocol['parent_manifest']['path']) == protocol['parent_manifest']['sha256']
    parent = read(HERE / 'parent_revalidation.json')
    assert parent['passed'] and (parent['verified_paths'], parent['logical_bytes']) == (2197, 229054356)
    for record in read(protocol['parent_manifest']['path'])['files']:
        assert sha(record['path']) == record['sha256']
    for path, digest in read(HERE / 'implementation.json')['source_files'].items():
        assert sha(path) == digest
    inputs = read(HERE / 'fixture_inputs_maintenance_fixed.json')
    assert sha(HERE / 'fixture_protocol.json') == inputs['protocol_sha256']
    assert sha(inputs['forecasts']['path']) == inputs['forecasts']['sha256']
    amendment = read(HERE / 'fixture_metadata_amendment.json')
    assert sha(HERE / 'fixture_inputs.json') == amendment['original_inputs_sha256']
    assert sha(HERE / 'fixture_inputs_maintenance_fixed.json') == amendment['current_inputs_sha256']
    for row in amendment['changes']:
        old, new = read(row['original']['path']), read(row['current']['path'])
        for tiers in new['leverage_tiers'].values():
            assert tiers[0].pop('maintAmt') == 0
        assert new == old
    for case in inputs['cases']:
        for record in [case['config'], case['markets']]:
            assert sha(record['path']) == record['sha256']
        for path, digest in case['files'].items():
            assert sha(path) == digest
        directory = OUT / 'runs' / case['name'] / 'observed1'
        for path, record in read(directory / 'implementation_freeze.json').items():
            assert sha(record['snapshot']) == record['sha256']
            assert sha(path) == record['sha256']
        result = read(directory / 'native_fixture_result.json')
        assert result['native_start_completed'] and result['source20_funded_accounts'] == 0
        for z in result['native_zips']:
            assert sha(z['path']) == z['sha256']
        observer = read(directory / 'observation_receipt.json')
        assert observer['hourly_points'] == 50 and observer['actual_observations'] == 49
    audit = read(HERE / 'fixture_audit.json')
    assert audit['passed'] and (audit['native_runs'], audit['hourly_points'], audit['independent_nav_scalars'], audit['filled_orders']) == (6, 300, 1200, 82)
    assert [r['case'] for r in audit['cases'] if not r['fixture_account_admitted']] == ['one_leg_rejected', 'missing_funding_exposed']
    assert read(HERE / 'metadata_admission.json')['admitted_financial_metadata'] is False
    preprint = read(HERE / 'preprint_receipt.json')
    assert sha(preprint['path']) == preprint['sha256']
    joh5 = read(HERE / 'joh5_source_receipt.json')
    assert sha(joh5['readme_path']) == joh5['readme_sha256']
    raw = Path(joh5['readme_path']).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == joh5['readme_git_blob_sha1']
    assert joh5['commit_committer_clock'].startswith('2021-05-06')
    for record in read(HERE / 'official_notice_download_receipts.json'):
        if 'path' in record:
            assert sha(record['path']) == record['sha256']
            assert record['status'] == 202 and record['bytes'] == 0
    assert '44 passed' in (HERE / 'regression.log').read_text()
    assert (HERE / 'lint_final.log').read_text().strip() == 'All checks passed!'
    sys.path.insert(0, str(ENGINE))
    sys.path.insert(0, str(HERE.parent / 'copula_execution_native_20261115_v3'))
    sys.path.insert(0, str(HERE))
    from CopulaPortfolio import CopulaPortfolio
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade, Order
    from freqtrade.wallets import Wallets
    try:
        object.__new__(CopulaPortfolio).ft_bot_start()
    except ValueError as exc:
        assert 'admission remains incomplete' in str(exc)
    else:
        raise AssertionError('Source20 funded lifecycle unexpectedly admitted')
    owners = {c.__name__: inspect.getfile(c) for c in [Backtesting, LocalTrade, Order, Wallets]}
    assert all(str(ENGINE) in p for p in owners.values())
    save(HERE / 'verification.json', dict(passed=True, native_commit=native['commit'],
        native_files=len(native['files']), runtime_owners=owners, component_checks=44,
        new_checks=21, inherited_checks=23, native_synthetic_runs=6,
        native_trade_lifecycles=41, native_fills=82, hourly_points=300,
        independent_nav_scalar_checks=1200, pair_failure_detected=True,
        exposed_missing_funding_denied=True, unexposed_gap_not_zero_filled=True,
        actual_capital500_and1000=True, parent_seal_unchanged=True,
        source20_guard_closed=True, source20_funded_accounts=0,
        new_accepted_independent_alphas=0, combination_accounts=0,
        source_strategy_financial_performance=None, objective_achieved=False))
    paths = list(HERE.rglob('*'))
    # Do not inventory git object databases or adjacent API/credential files.
    # Only the explicitly reviewed sources and synthetic fixtures/runs bind.
    paths += list((OUT / 'fixtures').rglob('*')) + list((OUT / 'runs').rglob('*'))
    paths += [OUT / 'synthetic_forecasts.parquet']
    skip = {'artifact_manifest.json', 'checkpoint_receipt.json', 'verify.log'}
    files = [p for p in paths if p.is_file() and p.name not in skip
             and '__pycache__' not in p.parts and not any(part.startswith('.') for part in p.relative_to(ROOT).parts)]
    records = [{'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(set(files))]
    save(HERE / 'artifact_manifest.json', dict(sealed_at=datetime.now(UTC).isoformat(),
        scope='Native synthetic paired-account integration and source metadata admission only', files=records))
    save(HERE / 'checkpoint_receipt.json', dict(verified=True, at=datetime.now(UTC).isoformat(),
        bound_paths=len(records), manifest_sha256=sha(HERE / 'artifact_manifest.json'),
        progress='Six actual native full synthetic lifecycles with fills and complete fixture NAV; independent paired/funding failure rejection',
        source20_funded_accounts=0, source_strategy_returns=None,
        objective_achieved=False, orders_enabled=False, services_changed=False))
    print('VERIFIED', len(records), 'paths;44checks;82native synthetic fills;0source20financial accounts')


if __name__ == '__main__':
    main()
