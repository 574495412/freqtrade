"""Verify the precise current preparation scope and immutable parent studies."""
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_execution_native_20261115_v3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    engine = ROOT.parent / 'CZ-Agent/vendor/freqtrade'
    manifest = read(engine.parent / 'FREQTRADE_SOURCE.json')
    assert manifest['commit'] == 'c051e9065c5b677f03817dd1f83cb980af1ed684'
    for name, digest in manifest['files'].items(): assert sha(engine / name) == digest, name
    assert {str(p.relative_to(engine)) for p in engine.rglob('*.py')} == {p for p in manifest['files'] if p.endswith('.py')}
    parents = []
    for stem in ['copula_reference_native_20261114_v1', 'copula_account_native_20261115_v2']:
        parent = HERE.parent / stem / 'artifact_manifest.json'
        seal = read(parent)
        for rec in seal['files']: assert sha(rec['path']) == rec['sha256'], rec['path']
        parents.append(dict(path=str(parent), sha256=sha(parent), files=len(seal['files'])))
    frozen = read(HERE / 'execution_implementation_freeze_flags.json')
    for path, digest in frozen.items(): assert sha(path) == digest, path
    amendment = read(HERE / 'technical_amendment.json')
    for path, digest in amendment['preserved_inputs_and_initial_owners'].items(): assert sha(path) == digest
    assert sha(amendment['current_owner']['path']) == amendment['current_owner']['sha256']
    first = read(HERE / 'native_preflight.json')
    second = read(HERE / 'native_preflight_revalidated.json')
    latest = read(HERE / 'native_preflight_flag_capture.json')
    assert first == second
    stripped = [{k:v for k,v in r.items() if k != 'native_leg_flags'} for r in latest['records']]
    assert stripped == first['records']
    assert latest['passed'] and latest['native_conversion_calls'] == 60
    assert [r['native_shifted_leg_flags'] for r in latest['records']] == [210, 234, 262]
    for r in latest['records']: assert sha(r['native_leg_flags']['path']) == r['native_leg_flags']['sha256']
    transport = read(HERE / 'native_intent_verification.json')
    assert transport['passed'] and transport['common_hourly_nominal_checks'] == 43776
    assert transport['weekly_boundary_checks'] == 522 and transport['actual_transport_prefix_recalculations'] == 18
    provider = read(HERE / 'native_provider_verification.json')
    assert provider['passed'] and provider['native_data_provider_checks'] == 80
    assert provider['independent_exact_value_cells'] == 19680742
    reconciliation = read(HERE / 'input_reconciliation.json')
    assert reconciliation['passed_within_raw_native_and_public_api_scope']
    assert reconciliation['monthly_archives_verified'] == 670 and reconciliation['daily_mark_archives_verified'] == 6
    assert reconciliation['actual_daily_mark_rows_inserted'] == 144
    assert reconciliation['public_funding_rows'] == 39412
    assert sha(HERE / 'input_admission.json') == reconciliation['original_input_admission_sha256']
    issues = reconciliation['cadence_issue_classifications']
    assert len(issues) == 4 and sum(r['classification'] == 'unresolved_public_event_gap' for r in issues) == 2
    assert all(not r['zero_rate_assumed'] and not r['actual_native_held_exposure_verified'] for r in issues)
    # Verify all prepared data/raw receipt hashes without changing past receipts.
    admission = read(HERE / 'input_admission.json')
    for row in admission['raw_receipts']:
        for key in ['archive', 'checksum', 'prepared']: assert sha(row[key]['path']) == row[key]['sha256']
        for repair in row.get('daily_repairs', []):
            for key in ['archive', 'checksum']: assert sha(repair[key]['path']) == repair[key]['sha256']
    for name, digest in read(OUT / 'data_receipts.json').items(): assert sha(OUT / 'candles' / name) == digest
    metadata = read(HERE / 'metadata_admission.json')
    assert not metadata['funded_admission'] and not metadata['minimum_notional_certified']
    assert metadata['EOS_binance_cryptofuture_reference_rows'][0]['minimum_order_size'] == ''
    tick = read(HERE / 'EOS_reference_tick_diagnostic.json')
    assert sha(tick['price_source']) == tick['price_sha256']
    assert all(r['off_LEAN_reference_tick_0_001'] == 0 for r in tick['results'])
    # Exact captured bytes, including denied/empty responses, are evidence of
    # what was retrieved, not automatic successful source admission.
    for filename in ['supplemental_metadata_receipts.json', 'lean_model_receipts.json', 'lean_binance_fixture_receipts.json',
                     'funding_study_receipts.json', 'funding_study_follow_receipts.json']:
        for rec in read(HERE / filename):
            if 'path' in rec: assert sha(rec['path']) == rec['sha256']
    for filename in ['nautilus_metadata_receipt.json', 'binance_minimum_notice_receipt.json']:
        rec = read(HERE / filename)
        if 'path' in rec: assert sha(rec['path']) == rec['sha256']
    assert re.search(r'\b23 passed\b', (HERE / 'execution_tests_final.log').read_text())
    for filename in ['owner_lint_final.log', 'research_lint_final.log']:
        assert (HERE / filename).read_text().strip() == 'All checks passed!'
    assert not list(OUT.glob('**/native_results/*.zip'))
    result = dict(passed_within_preparation_scope=True, native_commit=manifest['commit'], native_files_verified=len(manifest['files']),
        parent_seals=parents, local_owner_sha256=sha(ROOT / 'freqtrade/data/copula_execution.py'),
        component_tests=23, native_conversion_calls=60, native_shifted_flags=706, nominal_hourly_state_checks=43776,
        weekly_boundary_checks=522, transport_prefix_recalculations=18, provider_reads=80,
        provider_exact_value_cells=19680742, monthly_archives=670, daily_repair_archives=6, funding_events=39412,
        original_funding_issues_preserved=4, unresolved_public_event_gaps=2, nominal_exposure_is_not_native_fill_exposure=True,
        EOS_partial_metadata_only=True, financial_admission=False, new_native_funded_accounts=0,
        accepted_new_independent_factors=0, new_combination_accounts=0, financial_returns='UNKNOWN', goal_achieved=False)
    (HERE / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print('VERIFIED670monthly+6daily,80providerreads,706nativeflags,43776nominalstates,23tests;0newfundedaccounts')


if __name__ == '__main__':
    main()
