"""Read-only native provenance/account integrity gates after economic results."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())


def verify_manifest(path,count):
    manifest=read(path)
    assert len(manifest['files'])==count
    for row in manifest['files']:assert sha(row['path'])==row['sha256'],row['path']
    return {'path':str(path),'sha256':sha(path),'verified_paths':count}


def main():
    plan=read(HERE/'plan.json');frozen=read(HERE/'implementation_freeze.json')
    for path,digest in frozen.items():assert sha(path)==digest,path
    engine=read(plan['engine_manifest']['path'])
    assert engine['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684' and len(engine['files'])==595
    for path,digest in engine['files'].items():assert sha(Path(plan['engine_source'])/path)==digest,path
    data=read(HERE/'data_receipts.json')
    for name,digest in data.items():assert sha(Path(plan['datadir'])/name)==digest,name
    parents=[verify_manifest(HERE.parent/'nfi_carry_matched_native_20261115_v1/artifact_manifest.json',312),
             verify_manifest(HERE.parent/'paired_carry_reservation_native_20261115_v2/artifact_manifest.json',633),
             verify_manifest(HERE.parent/'heracles_source_native_20261116_v1/artifact_manifest.json',109),
             verify_manifest(HERE.parent/'heracles_source_native_20261116_v2/artifact_manifest.json',83)]
    results=read(HERE/'results.json');runs=read(HERE/'execution_receipt.json')
    assert len(runs)==4 and all(r['returncode']==0 for r in runs)
    assert results['actual_native_trades']==424 and results['actual_fills']==848
    assert results['common_endpoint_fills']==844 and results['new_hour_points']==58368
    assert len(results['portfolios'])==4 and all(not r['passed'] for r in results['portfolios'])
    assert all(not r['passed'] for r in results['standalone_gates'])
    assert results['maximum_independent_nav_error_usdt']<1e-8
    assert results['maximum_native_quote_error_usdt']<1e-8
    assert sum(r['native_quote_rows_audited'] for r in results['accounts'])==14592
    assert sum(r['native_base_rows_audited'] for r in results['accounts'])==26332
    signals=read(HERE/'signal_path_review.json')
    assert signals['source_signal_hashes_equal_in_all_four_accounts']
    assert all(not r['same_native_trade_paths'] for r in read(HERE/'cost_path_review.json'))
    assert '9 passed' in (HERE/'tests_final.log').read_text()
    # Bind all current native signals/quotes to the actual preflight full axes.
    prefix=read(HERE/'preflight.json')
    assert prefix['passed'] and prefix['actual_prefix_cells_including_repeated_prefixes']==71316
    maximum_rss=0
    zip_rows=[]
    for candidate in plan['candidates']:
        directory=Path(plan['output_root'])/(candidate['name']+f"_{candidate['cost_bps']}bps")
        assert sha(directory/'result.json')==next(r['result_sha256'] for r in runs if r['account']==directory.name)
        stats=read(directory/'independent_account_audit.json')
        assert stats['passed'] and stats['post_loop_terminal_force_exits']==1
        maximum_rss=max(maximum_rss,read(directory/'progress.json')['maximum_rss_bytes'])
        archive=next((directory/'native_results').glob('*.zip'))
        assert sha(archive)==stats['archive_sha256']
        zip_rows.append({'path':str(archive),'sha256':sha(archive)})
    result={'verified_at':datetime.now(UTC).isoformat(),'financial_frozen_paths_unchanged':len(frozen),
            'native_engine_files_unchanged':595,'native_history_files_unchanged':6,'parent_seals':parents,
            'completed_native_wallets':4,'preserved_failed_financial_attempts':2,'new_native_zips':zip_rows,
            'actual_native_trades':424,'natural_completed_trade_records':420,
            'simulated_fills':848,'post_loop_force_diagnostics':4,'hourly_nav_points':58368,
            'native_quote_rows_audited':14592,'native_base_rows_audited':26332,
            'maximum_independent_nav_error_usdt':results['maximum_independent_nav_error_usdt'],
            'maximum_native_quote_error_usdt':results['maximum_native_quote_error_usdt'],
            'rss_250ms_sample_max_bytes':maximum_rss,'tests_passed':9,
            'standalone_numeric_gates_passed':0,'fixed_portfolios_passed':0,
            'new_independent_alphas_accepted':0,'new_factor_analysis':0,'new_vibe_benches':0,
            'higher_comparable_net_return_established':False,'goal_complete':False,
            'real_orders_enabled':False,'production_eligible':False,'independent_oos':False,
            'window':['2025-01-01T00:00:00Z','2026-08-31T23:00:00Z']}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print('VERIFIED4wallets/848fills/58368hours;all economic gates failed;goal incomplete',flush=True)


if __name__=='__main__':main()
