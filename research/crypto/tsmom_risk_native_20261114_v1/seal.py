"""Seal a complete native research matrix while preserving unmet alpha goal."""
import hashlib
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE/'v002/plan.json').read_text())
    result = json.loads((HERE/'v002/results.json').read_text())
    audit = json.loads((HERE/'v002/native_validation.json').read_text())
    figure = json.loads((HERE/'figure_review.json').read_text())
    assert len(result['accounts']) == audit['accounts'] == 12 and audit['passed']
    assert figure['passed'] and figure['image_sha256'] == sha(HERE/'account_curves.png')
    assert '14 passed' in (HERE/'tests_combined.log').read_text()
    assert json.loads((HERE/'v002/full_native_clock_parity.json').read_text())['passed']
    for candidate in plan['candidates']:
        verify(plan,candidate)
    for record in json.loads((HERE/'source_manifest.json').read_text())['files']:
        assert sha(record['path']) == record['sha256']
    admission = json.loads((HERE/'input_admission.json').read_text())
    for key in ['funding_limit','funding_events','protocol','market_snapshot','data_receipts']:
        record = admission[key]
        assert sha(record['path']) == record['sha256'], key
    for record in admission['source_links']:
        assert sha(record['target']) == record['sha256']
        if 'source' in record:
            assert sha(record['source']) == record['sha256']
    root = Path(json.loads((HERE/'plan.json').read_text())['output_root'])
    interrupted = root/'CovarianceRiskTS_500_15bps'
    assert json.loads((interrupted/'process_receipt.json').read_text())['returncode'] == -2
    archived = json.loads((interrupted/'interrupted_cache_archive_receipt.json').read_text())
    assert sha(archived['archive']) == archived['sha256']
    with tarfile.open(archived['archive'],'r:gz') as cache:
        for name,record in archived['files'].items():
            raw = cache.extractfile('lossless_cache/'+name).read()
            assert len(raw) == record['bytes'] and hashlib.sha256(raw).hexdigest() == record['sha256']
    native_receipts = []
    for folder in [root,Path(plan['output_root'])]:
        for path in folder.glob('*/process_receipt.json'):
            record = json.loads(path.read_text())
            if record['returncode'] == 0:
                assert sha(path.parent/'result.json') == record['result_sha256']
            native_receipts.append(record)
    assert sum(r['returncode']==0 for r in native_receipts) == 13
    assert sum(r['returncode']!=0 for r in native_receipts) == 1
    paths = []
    for directory in [HERE,root]:
        paths += [p for p in directory.rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.lock'}
                  and p.name != 'completion_receipt.json']
    paths = sorted(set(paths))
    exposed = [a['account'] for a in audit['audits'] if a['marked']['tia_missing_event_actual_exposure']]
    receipt = dict(sealed_at_utc=datetime.now(UTC).isoformat(),
        research_matrix_complete=True, objective_complete=False,
        fixed_unique_account_configurations=12, completed_native_runs=13,
        duplicated_technical_parity_runs=1, interrupted_runs_preserved=1,
        actual_native_model_orders=sum(a['marked']['filled_orders'] for a in audit['audits']),
        unique_account_hourly_points=12*14592, source_weight_configurations=3,
        new_accepted_independent_factors=0, new_deployable_strategies=0,
        native_factor_analysis_calls=0, strategy_allocation_study=True,
        native_vibe_benches_claimed=0, actual_private_settlement_certified=False,
        known_tia_gap_exposed_accounts=exposed,
        historical_rule_certification=False, independent_oos=False,
        official_core_files_verified=595, tests_passed=14,
        preserved_failures=['Initial BNB filtered-snapshot admission',
                            'Author-method AST extraction and Pandas3 readonly-array tests',
                            'Interrupted v1 second account with actual exitcode-2 and archived cache',
                            'Clocked registration Parquet index-frequency metadata assertion',
                            'Initial figure reader expected date column for indexed combo Parquet',
                            'Initial audit completeness metadata overclaim, corrected without financial changes'],
        source_and_artifacts={str(p.resolve()):sha(p) for p in paths},
        real_orders=False, paper_worker_changes=False, deployment=False,
        running_service_changes=False, commit=False, push=False)
    save(HERE/'completion_receipt.json',receipt)
    for path,digest in receipt['source_and_artifacts'].items():
        assert sha(path) == digest, path
    print('SEALED',len(paths),'artifacts;12 unique native configurations,13 completed runs,1 preserved interruption; overall alpha goal incomplete')


if __name__ == '__main__':
    main()
