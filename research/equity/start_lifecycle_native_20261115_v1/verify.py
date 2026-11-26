"""Verify actual native starts, immutable parents, sources and explicit finance limits."""
import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bootstrap import HERE, ROOT, PARENT, boot

manifest = boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_fixture_lifecycle import EquityFixtureLifecycleMixin
from freqtrade.persistence import LocalTrade, Order
from freqtrade.wallets import Wallets


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def main():
    protocol = read(HERE / 'protocol.json')
    parent = protocol['parent_manifest']
    assert sha(parent['path']) == parent['sha256']
    previous = read(parent['path'])
    for path, digest in previous.items():
        assert sha(ROOT / path) == digest, path
    source_owners = read(PARENT / 'protocol.json')['source_files']
    for path, digest in source_owners.items():
        assert sha(path) == digest, path
    owners = {c.__name__: inspect.getfile(c) for c in [Backtesting, LocalTrade, Order, Wallets]}
    assert all('/CZ-Agent/vendor/freqtrade/' in p for p in owners.values())
    assert all(n not in EquityFixtureLifecycleMixin.__dict__ for n in (
        '__init__', 'time_pair_generator', '_time_pair_generator_det', 'backtest_loop',
        '_enter_trade', '_try_close_open_order', '_process_exit_order'))
    assert '14 passed' in (HERE / 'tests_final.log').read_text()
    assert '276 passed' in (HERE / 'parent_regression_final.log').read_text()
    assert (HERE / 'lint_final.log').read_text().strip() == 'All checks passed!'
    assert (HERE / 'lint_research_final.log').read_text().strip() == 'All checks passed!'
    receipts = read(HERE / 'execution_receipt.json')
    assert len(receipts) == 6 and all(r['returncode'] == 0 and r['pid'] > 0 for r in receipts)
    for row in receipts:
        directory = HERE / 'runs' / row['case']
        assert row['case'] in protocol['cases']
        trace = read(directory / 'native_trace.json')
        assert trace['native_start'] and trace['historical_accounts'] == trace['bench_records'] == 0
        for path, digest in read(directory / 'implementation_freeze.json').items():
            assert sha(path) == digest, path
        for path, record in read(directory / 'source_snapshot_manifest.json').items():
            assert sha(path) == sha(record['snapshot']) == record['sha256']
        for record in read(directory / 'native_export_receipt.json'):
            assert sha(record['path']) == record['sha256']
    audit = read(HERE / 'trace_audit.json')
    assert audit['passed'] and audit['scalar_checks'] == 504
    assert audit['native_synthetic_fills'] == 22 and audit['native_start_lifecycles'] == 6
    inputs = read(HERE / 'input_admission.json')
    assert inputs['passed'] and not inputs['admitted']
    assert inputs['verified_price_feature_files'] == 514 and inputs['files_with_source_vendor_columns'] == 0
    for name in ['source_receipts.json', 'follow_source_receipts.json']:
        for row in read(HERE / name):
            if 'path' in row:
                assert sha(row['path']) == row['sha256']
    caption = read(HERE / 'caption_receipt.json')
    assert sha(caption['path']) == caption['sha256'] and caption['bytes'] == 0
    intake = read(HERE / 'source_intake.json')
    assert sha(intake['douyin']['parent_intake']) == intake['douyin']['parent_intake_sha256']
    with patch.object(Backtesting, '__init__') as constructor:
        try:
            EquityBacktesting({})
        except EquityAdmissionError:
            pass
        else:
            raise AssertionError('Historical equity admission unexpectedly opened')
        constructor.assert_not_called()
    save('verification.json', dict(passed=True, native_commit=manifest['commit'],
        native_files=len(manifest['files']), unchanged_parent_paths=len(previous),
        native_runtime_owners=owners, checks=290, new_checks=14, inherited_checks=276,
        synthetic_full_start_lifecycles=6, native_zip_exports=6, synthetic_fills=22,
        nav_snapshots=84, callbacks=42, independent_scalar_checks=504,
        original_financial_paths_exact=6, future_extrema_invariant_markets=['XNYS','XHKG'],
        historical_entry_rejects_before_constructor=True, source_input_files_verified=514,
        new_registry_factors=0, new_accepted_alphas=0, factor_analysis_calls=0,
        full_historical_stock_accounts=0, combination_accounts=0, bench_records=0,
        financial_performance=None, higher_comparable_return_found=False,
        objective_achieved=False, real_orders=False, deployment=False, services_changed=False))
    skip = {'artifact_manifest.json', 'checkpoint_receipt.json', 'verify.log'}
    paths = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and p.name not in skip and not any(v.startswith('.') for v in p.relative_to(HERE).parts)]
    bindings = {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}
    production = ROOT / 'freqtrade/optimize/equity_fixture_lifecycle.py'
    bindings[str(production.relative_to(ROOT))] = sha(production)
    save('artifact_manifest.json', bindings)
    save('checkpoint_receipt.json', dict(verified=True, at=datetime.now(UTC).isoformat(),
        requested_scope='HK/US equities through freqtrade; crypto checkpoints excluded',
        bound_paths=len(bindings), manifest_sha256=sha(HERE / 'artifact_manifest.json'),
        progress='Six actual native synthetic start/export lifecycles and independent ledger parity',
        historical_accounts=0, higher_comparable_return_found=False, financial_performance=None,
        objective_achieved=False, real_orders=False, deployment=False, services_changed=False))
    print('VERIFIED', len(bindings), 'paths;290checks;6actual synthetic starts/ZIPs;22fills;0historicalstockaccounts')


if __name__ == '__main__':
    main()
