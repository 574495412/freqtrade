"""Inspect actual run artifacts and preserve a narrow, falsifiable checkpoint."""
import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bootstrap import HERE, ROOT, boot

manifest = boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_sessions import EquitySessionRoutingMixin
from freqtrade.persistence import LocalTrade, Order
from freqtrade.wallets import Wallets


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(name):
    return json.loads((HERE / name).read_text())


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    for path, digest in read('protocol.json')['source_files'].items():
        assert sha(path) == digest
    for path, record in read('implementation.json')['local_native_modules'].items():
        assert sha(path) == sha(record['snapshot']) == record['sha256']
    amendment = read('technical_amendment.json')['native_order_field']
    assert sha(amendment['prior_owner']) == amendment['prior_sha256']
    assert sha(amendment['current_owner']) == amendment['current_sha256']
    for name in ['source_receipts.json', 'follow_source_receipts.json', 'data_source_receipts.json', 'calendar_source_receipts.json']:
        for row in read(name):
            path = row.get('path', HERE / 'sources' / row.get('name', ''))
            assert sha(path) == row['sha256']
    assert '276 passed' in (HERE / 'regression_initial.log').read_text()
    for name in ['lint_production.log', 'lint_research_final.log']:
        assert (HERE / name).read_text().strip() == 'All checks passed!'
    audit = read('trace_audit.json')
    assert audit['passed'] and (audit['scalar_checks'], audit['native_synthetic_fills']) == (504, 22)
    snapshots = 0
    for scenario in audit['scenarios']:
        directory = HERE / 'runs' / scenario['name']
        trace = json.loads((directory / 'native_trace.json').read_text())
        assert trace['native_constructor'] and trace['native_backtest'] and not trace['native_start']
        assert trace['historical_accounts'] == 0
        snapshots += len(trace['opens']) + len(trace['closes'])
        for path, digest in json.loads((directory / 'implementation_freeze.json').read_text()).items():
            assert sha(path) == digest
        for path, record in json.loads((directory / 'source_snapshot_manifest.json').read_text()).items():
            assert sha(path) == sha(record['snapshot']) == record['sha256']
    assert snapshots == 84
    source = read('source_causality_audit.json')
    assert source['passed'] and len(source['checks']) == 21
    assert {r['column'] for r in source['checks'] if r['changed']} == {'STR_Signal'}
    flow = read('hk_flow_input_readiness.json')
    assert not flow['admitted_for_prediction'] and len(flow['records']) == 11
    calendar = read('connect_calendar_reconciliation.json')
    assert calendar['passed'] and len(calendar['verified_missing_dates']) == 15
    assert calendar['initial_readiness_sha256'] == sha(HERE / 'hk_flow_input_readiness.json')
    assert all(cell['status'] == 'Closed' and sha(cell['path']) == cell['source_sha256'] for cell in calendar['verified_missing_dates'])
    with patch.object(Backtesting, '__init__') as native:
        try:
            EquityBacktesting({})
        except EquityAdmissionError:
            pass
        else:
            raise AssertionError('Incomplete historical stock account admitted')
        native.assert_not_called()
    owners = {c.__name__: inspect.getfile(c) for c in [Backtesting, LocalTrade, Order, Wallets]}
    assert all('/CZ-Agent/vendor/freqtrade/' in p for p in owners.values())
    assert 'time_pair_generator' not in EquitySessionRoutingMixin.__dict__
    engine = Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
    assert {str(p.relative_to(engine)) for p in engine.rglob('*.py')} == {p for p in manifest['files'] if p.endswith('.py')}
    save('verification.json', dict(passed=True, native_commit=manifest['commit'],
        native_files=len(manifest['files']), native_runtime_owners=owners,
        component_checks=276, new_checks=23, previous_checks=253,
        main_synthetic_scenarios=6, native_synthetic_fills=22, snapshots=84,
        independent_scalar_checks=504, source_prefix_field_checks=21,
        official_southbound_closed_cells=15, equity_entry_still_rejects=True,
        full_historical_stock_accounts=0, combinations=0, accepted_alphas=0,
        factor_analysis_calls=0, bench_records=0, financial_performance=None,
        objective_achieved=False))
    skip = {'artifact_manifest.json', 'checkpoint_receipt.json', 'verify.log'}
    paths = [p for p in HERE.rglob('*') if p.is_file() and p.name not in skip
             and '__pycache__' not in p.parts and not any(v.startswith('.') for v in p.relative_to(HERE).parts)]
    bindings = {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}
    for path in read('implementation.json')['local_native_modules']:
        p = Path(path)
        bindings[str(p.relative_to(ROOT))] = sha(p)
    save('artifact_manifest.json', bindings)
    save('checkpoint_receipt.json', dict(at=datetime.now(UTC).isoformat(), verified=True,
        scope='HK/US requested continuation; execution/source checkpoint only',
        manifest_sha256=sha(HERE / 'artifact_manifest.json'), bound_paths=len(bindings),
        objective_achieved=False, full_historical_stock_accounts=0,
        higher_comparable_return_found=False, financial_performance=None,
        real_orders=False, deployment=False, services_changed=False))
    print('VERIFIED', len(bindings), 'paths; 276 checks; 22 native synthetic fills; 0 full historical stock accounts')


if __name__ == '__main__':
    main()
