"""Verify source/input boundaries and seal technical evidence; never fund an account."""
import hashlib
import inspect
import json
from pathlib import Path

from bootstrap import boot

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    engine = boot()
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data.equity_short_positions import parse_sfc_csv
    from freqtrade.data.equity_short_positions_provider import EquityShortPositionDataProvider
    from freqtrade.data.history.equity import EquityAdmissionError
    assert issubclass(EquityShortPositionDataProvider, DataProvider)
    native_path = inspect.getfile(DataProvider)
    assert native_path.startswith('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/')
    sources = read(HERE / 'source_receipts.json')
    for row in sources:
        assert sha(row['path']) == row['sha256']
    error = next(r for r in sources if r['path'].endswith('sfc_20250307.http403.html'))
    assert error['status'] == 403
    from datetime import date
    rejected = False
    try:
        parse_sfc_csv(Path(error['path']).read_bytes(), reporting_date=date(2025, 3, 7),
            source_url=error['url'], source_sha256=error['sha256'], published_at=None, observed_at=None)
    except EquityAdmissionError:
        rejected = True
    assert rejected, 'Actual HTTP403 bytes must never become report data'
    inputs = read(HERE / 'input_audit.json')
    assert inputs['original_files'] == 514 and inputs['HK'] == 11 and inputs['US'] == 503
    assert inputs['files_with_short_position_columns'] == 0
    for row in inputs['rows']:
        assert sha(row['source']) == row['source_sha256']
        assert sha(row['staged']) == row['staged_sha256']
    for row in read(HERE / 'ownership.json')['current_owner_files']:
        assert sha(row['path']) == row['sha256']
    parents = []
    for relative, expected, count in [
        ('research/equity/dividend_lifecycle_native_20261116_v1',
         'e1bed5162ab44d45ba4bf8c01bb6a4810baa033410a0ab30c02f9c708dc37426', 377),
        ('research/crypto/month_boundary_source_native_20261116_v1',
         '3c5e66823ccc8b79a0d7132eccc400d63de1d9e8a333e32d70a522af56346806', 396),
    ]:
        manifest = ROOT / relative / 'artifact_manifest.json'
        assert sha(manifest) == expected
        files = read(manifest)['files']
        assert len(files) == count
        for row in files:
            assert sha(row['path']) == row['sha256'], row['path']
        parents.append(dict(path=str(manifest), sha256=expected, checked_paths=count))
    assert '32 passed' in (HERE / 'tests_final.log').read_text()
    assert 'All checks passed!' in (HERE / 'lint_final.log').read_text()
    assert 'already formatted' in (HERE / 'format_final.log').read_text()
    save('verification.json', dict(passed=True, evidence_tier='native input technical only',
        native_engine=engine, actual_native_data_provider_path=native_path,
        original_source_and_staged_price_files_verified=1028,
        original_weekly_csv_acquired=False, actual403error_bytes_rejected=True,
        new_unique_tests=32, test_replays_not_independent_samples=True,
        source_script_executed=False, source_license_text_acquired=False,
        new_factor_definitions=0, factor_analysis=0, native_historical_stock_accounts=0,
        native_synthetic_funded_accounts=0, new_portfolio_accounts=0, bench=0,
        accepted_independent_alpha=0, stock_net_return=None, stock_cagr=None,
        independent_oos=False, production_eligible=False, orders_enabled=False,
        services_changed=False, parent_seals=parents))
    save('goal_audit.json', dict(goal_complete=False, status='active',
        current_scope='HK/US as requested;crypto results separate',
        progress='distinct reported inventory source review and local native input/provider32checks',
        remaining='original weekly bytes and visibility,breadth/method admission,Registry/FA and complete native stock account economics',
        higher_comparable_stock_net_return_established=False, accepted_independent_alpha=0,
        no_parameter_rescue=True, no_orders_or_service_changes=True))
    excluded = {'artifact_manifest.json', 'checkpoint_receipt.json'}
    paths = {p for p in HERE.rglob('*') if p.is_file() and p.name not in excluded
             and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    paths.update(ROOT / ('freqtrade/data/' + name) for name in [
        'equity_short_positions.py', 'equity_short_positions_provider.py', 'equity_short_positions.md'])
    rows = [dict(path=str(p), bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(paths)]
    save('artifact_manifest.json', dict(scope='post-result technical input checkpoint;not preregistration or account returns', files=rows))
    for row in rows:
        assert sha(row['path']) == row['sha256']
    receipt = dict(manifest_sha256=sha(HERE / 'artifact_manifest.json'),
        verified_paths=len(rows), new_native_stock_accounts=0, goal_complete=False)
    save('checkpoint_receipt.json', receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
