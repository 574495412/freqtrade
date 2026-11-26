"""Bind current owners, actual outputs and limits; no invented account metrics."""
import inspect
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

from bootstrap import boot, HERE, ROOT
from prepare import sha, save

manifest = boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_sessions import EquitySessionRoutingMixin
from freqtrade.persistence import LocalTrade, Order
from freqtrade.wallets import Wallets


def main():
    protocol = json.loads((HERE / 'protocol.json').read_text())
    amend = json.loads((HERE / 'technical_amendment.json').read_text())
    assert sha(amend['prior_owner_copy']) == amend['prior_owner_sha256']
    assert sha(amend['initial_audit']) == amend['initial_audit_sha256']
    for path, expected in amend['current_components'].items(): assert sha(path) == expected, path
    for key in ['parent_inputs', 'parent_admission']: assert sha(protocol[key]) == protocol[key + '_sha256']
    a = json.loads((HERE / 'input_routing_audit.json').read_text())
    initial = json.loads((HERE / 'input_routing_audit_initial.json').read_text())
    assert a == initial and a['passed']
    assert (a['original_inputs'], a['admitted'], a['rejected'], a['routing_rows']) == (514, 486, 28, 202088)
    assert a['callback_checks'] == 2916
    for rec in json.loads((HERE / 'source_receipts.json').read_text()):
        if 'path' in rec: assert sha(rec['path']) == rec['sha256']
    for rec in json.loads((HERE / 'caption_receipts.json').read_text()):
        assert sha(rec['path']) == rec['sha256'] and rec['bytes'] == 0
    assert (HERE / 'sources/overnight_pinned_tree.txt').read_text().splitlines() == ['README.md', 'docs/methodology.md']
    mapping = {'README.md': 'overnight_README.md', 'docs/methodology.md': 'overnight_methodology.md'}
    for line in (HERE / 'sources/overnight_pinned_objects.txt').read_text().splitlines():
        metadata, name = line.split('\t')
        payload = (HERE / 'sources' / mapping[name]).read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest() == metadata.split()[2]
    for path, expected in json.loads((HERE / 'existing_factor_receipts.json').read_text()).items():
        assert sha(path) == expected
    trace = json.loads((HERE / 'native_clock_trace.json').read_text())
    assert trace['passed'] and trace['native_synthetic_fills'] == 4
    assert trace['generator_yields'] == 14 and trace['snapshots'] == 16
    assert all(s['native_generator'] == 'freqtrade.optimize.backtesting' for s in trace['scenarios'])
    log = (HERE / 'regression_final.log').read_text()
    assert re.search(r'\b253 passed\b', log) and 'FAILED' not in log
    for name in ['lint_final.log', 'semantic_lint.log']:
        assert (HERE / name).read_text().strip() == 'All checks passed!'
    with patch.object(Backtesting, '__init__') as native:
        try: EquityBacktesting({})
        except EquityAdmissionError: pass
        else: raise AssertionError('Incomplete stock account admitted')
        native.assert_not_called()
    paths = {c.__name__: inspect.getfile(c) for c in [Backtesting, LocalTrade, Order, Wallets]}
    assert all('/CZ-Agent/vendor/freqtrade/' in p for p in paths.values())
    assert 'time_pair_generator' not in EquitySessionRoutingMixin.__dict__
    engine = Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
    assert {str(p.relative_to(engine)) for p in engine.rglob('*.py')} == {p for p in manifest['files'] if p.endswith('.py')}
    save('verification.json', dict(passed=True, native_commit=manifest['commit'], native_files=len(manifest['files']),
        native_runtime_owners=paths, inherited_native_pair_generator=True, unique_component_checks=253,
        new_session_checks=26, inherited_checks=227, real_input_rows=202088, real_callback_checks=2916,
        native_synthetic_fills=4, native_synthetic_generator_yields=14, synthetic_snapshots=16,
        original_inputs=514, admitted=486, rejected=28, exact_technical_rerun=True,
        original_parent_modules_unchanged=True, complete_equity_entry_rejects=True,
        historical_strategy_accounts=0, combination_accounts=0, factor_analysis_calls=0, bench_records=0,
        financial_performance=None, objective_achieved=False))
    paths = sorted(p for p in HERE.rglob('*') if p.is_file() and not any(part.startswith('.') for part in p.relative_to(HERE).parts) and p.name not in {'artifact_manifest.json', 'completion_receipt.json', 'verify.log'})
    bindings = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    owner = ROOT / 'freqtrade/optimize/equity_sessions.py'
    bindings[str(owner.relative_to(ROOT))] = sha(owner)
    save('artifact_manifest.json', bindings)
    print('VERIFIED', len(bindings), 'paths; original native engine, inputs and preserved failures; 0 historical stock accounts')


if __name__ == '__main__':
    main()
