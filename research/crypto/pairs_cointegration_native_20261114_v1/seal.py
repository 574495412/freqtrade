"""Verify matrix completion while retaining the unmet overall research goal."""
import json
from datetime import UTC,datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import sha,save
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    verify(plan,plan['candidates'][0])
    validation=json.loads((HERE/'native_validation.json').read_text());results=json.loads((HERE/'results.json').read_text())
    assert validation['passed'] and validation['accounts']==12 and len(results['accounts'])==12
    assert validation['hourly_account_points']==175104 and len(results['combinations'])==4
    assert json.loads((HERE/'execution_complete.json').read_text())['accounts']==12
    assert '9 passed' in (HERE/'tests.log').read_text()
    review=json.loads((HERE/'figure_review.json').read_text())
    assert review['passed'] and review['sha256']==sha(HERE/'account_curves.png')
    source=json.loads((HERE/'source_manifest.json').read_text())
    for r in source['files']:assert sha(r['path'])==r['sha256']
    for path,digest in json.loads((HERE/'dependency_manifest.json').read_text()).items():assert sha(path)==digest
    native_accounts=[];trades=orders=0
    for candidate in plan['candidates']:
        assert sha(candidate['config']['path'])==candidate['config']['sha256']
        for cost in plan['total_cost_bps_per_side']:
            path=out/f'{candidate["name"]}_{cost}bps';receipt=json.loads((path/'process_receipt.json').read_text())
            assert receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json')
            assert not (path/'failure.json').exists() and not (path/'resource_guard.json').exists()
            assert not (path/'lossless_cache').exists() and (path/'generated_cache_inventory.json').exists()
            assert (path/'pair_source_audit.json').exists() and (path/'independent_native_audit.json').exists()
            row=next(r for r in results['accounts'] if r['account']==candidate['name'] and r['cost_bps_per_side']==cost)
            trades+=row['native_trades'];orders+=row['filled_orders']
            native_accounts.append(dict(account=path.name,receipt_sha256=sha(path/'process_receipt.json'),
                                        returncode=0,filled_orders=row['filled_orders']))
    assert orders==validation['total_native_fills']
    protocol=json.loads((HERE/'protocol.json').read_text());freeze=json.loads((HERE/'protocol_freeze.json').read_text())
    assert sha(HERE/'protocol.json')==freeze['protocol']['sha256']
    assert protocol['account_start']==plan['start'] and protocol['account_end']==plan['comparison_end']
    artifacts={}
    for root in [HERE,out]:
        for p in sorted(root.rglob('*')):
            if not p.is_file() or any(x in p.parts for x in ['__pycache__','.pytest_cache','dependencies']):continue
            if p.name=='completion_receipt.json':continue
            if p.name.startswith('seal_') and p.suffix=='.log':continue
            artifacts[str(p)]=sha(p)
    result=dict(sealed_utc=datetime.now(UTC).isoformat(),research_matrix_complete=True,objective_complete=False,
                fixed_unique_native_accounts=12,completed_native_runs=12,preserved_financial_failures=0,
                native_model_lifecycles=trades,native_model_fills=orders,account_hourly_points=175104,
                actual500_sleeve_combinations=4,official_core_files_verified=595,tests_passed=9,
                source_naive_selected_pairs=13,adjusted_selected_pairs=4,new_mechanism_candidates=1,
                source_pair_views_not_independent_factors=True,passed_combined_numeric_gates=sum(g['combined_numeric_gate'] for g in results['gates']),
                new_accepted_independent_factors=0,native_factor_analysis_calls=0,native_vibe_benches_claimed=0,
                accepted_deployable_strategies=0,independent_oos=False,private_settlement_certified=False,
                orders=False,paper_worker_changes=False,service_changes=False,deployment=False,commit=False,push=False,
                preserved_technical_failures=['Initial native namespace lookup','Initial two timezone test fixtures','Initial chart endpoint collision',
                                             'Initial seal included its own live stdout; final seal excludes seal logs'],
                unsealed_files='Only the completion receipt itself, live seal stdout logs, Python/pytest caches; dependencies separately verified by manifest',
                native_accounts=native_accounts,source_and_artifacts=artifacts)
    save(HERE/'completion_receipt.json',result)
    print(f'SEALED {len(artifacts)} artifacts;12 accounts;{trades} lifecycles;{orders} fills;objective incomplete',flush=True)


if __name__=='__main__':main()
