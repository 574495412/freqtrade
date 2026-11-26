"""Evidence audit of actual native executions, immutable models, and failed gates."""
import hashlib,json,re
from datetime import UTC,datetime
from pathlib import Path
import pandas as pd
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify as native_verify
from research.crypto.band_trend_native_20261113_v1.analyze import read_native,combine,metrics
HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
def main():
    plan=read(HERE/'plan.json');out=Path(plan['output_root']);results=read(HERE/'results.json');validation=read(HERE/'native_validation.json')
    assert read(HERE/'execution_complete.json')['accounts']==16
    assert validation['passed']and len(validation['audits'])==len(results['accounts'])==16
    assert len(results['combinations'])==4
    assert re.search(r'\b12 passed\b',(HERE/'tests_final.log').read_text())
    assert (HERE/'semantic_lint.log').read_text().strip()=='All checks passed!'
    pre=read(HERE/'preflight.json');assert pre['passed']and pre['native_original_files_verified']==595
    assert len(pre['source_models'])==6 and pre['independent_forward_probability_cells']==3873
    assert pre['maximum_forward_probability_error']<1e-10
    assert len(pre['actual_causal_refit_checks'])==3 and pre['actual_source_last_fold_repeat']
    for r in read(HERE/'source_manifest.json')['records']:assert sha(r['path'])==r['sha256']
    for r in read(HERE/'social_receipts.json'):
        if 'path'in r:assert sha(r['path'])==r['sha256']
        if 'visible_path'in r:assert sha(r['visible_path'])==r['visible_sha256']
        if 'caption'in r:assert sha(r['caption']['path'])==r['caption']['sha256']and r['caption']['bytes']==0
    navs={};orders=0;hours=0;budgets=0
    for c in plan['candidates']:
        native_verify(plan,c)
        for cost in plan['total_cost_bps_per_side']:
            key=f'{c["name"]}_{cost}bps';p=out/key;rec=read(p/'process_receipt.json')
            assert rec['returncode']==0 and sha(p/'result.json')==rec['result_sha256']
            assert not(p/'failure.json').exists()and not(p/'resource_guard.json').exists()
            assert read(p/'callback_audit.json')['passed']
            stats,archive,cfg=read_native(p,c['strategy'])
            audit=next(a for a in validation['audits']if a['account']==key)
            assert sha(archive)==audit['archive_sha256']
            assert audit['cash']['passed']and audit['marked']['passed']and audit['policy']['passed']
            assert audit['marked']['complete_BTC_supplied_funding_grid']
            assert audit['marked']['maximum_nav_error_usdt']<3e-5
            orders+=audit['marked']['filled_orders'];hours+=audit['marked']['hourly_points'];budgets+=audit['policy']['daily_budget_snapshots']
            nav=pd.read_parquet(p/'hourly_equity.parquet').set_index('date').hourly_mark_equity
            navs[key]=nav
            assert len(nav)==14592 and nav.iloc[0]==c['capital']
            assert cfg['dry_run_wallet']==c['capital']and cfg['fee']==cost/10000
            row=next(a for a in results['accounts']if a['account']==c['name']and a['cost_bps_per_side']==cost)
            assert abs(metrics(nav)['net_return']-row['periods'][0]['net_return'])<1e-12
    for row in results['combinations']:
        nav=combine(*(navs[k]for k in row['actual_wallets']))
        stored=pd.read_parquet(HERE/f'combo_{row["name"]}_{row["cost_bps_per_side"]}bps.parquet').hourly_mark_equity
        pd.testing.assert_series_equal(nav,stored,check_names=False,check_exact=True)
        assert abs(metrics(nav)['net_return']-row['periods'][0]['net_return'])<1e-12
        assert not row['numeric_gate']and not row['beats_best_matched_1000']
    assert not results['source_passes_both_costs']and not results['any_combination_passes_both_costs']
    assert not results['deployment_eligible']and not results['objective_complete']
    assert orders==1210 and hours==233472 and budgets==9728
    # Verify rejected private-rule lead; it did not launch a replacement account.
    private=HERE.parent/'walkforward_ensemble_native_20261114_v1'
    m=read(private/'source_manifest.json');assert not m['exact_zoo_and_parameters_public']and m['public_python_strategy_files']==0
    for r in m['records']:assert sha(r['path'])==r['sha256']
    assert sha(private/'source_tree.txt')==m['git_tree_sha256']
    plot=HERE/'native_nav_15bps.png';assert plot.read_bytes().startswith(b'\x89PNG')
    result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_original_files':595,
        'actual_native_accounts':16,'actual_funded_combination_cost_cases':4,'unique_component_tests':12,
        'actual_rolling_models':6,'actual_causal_refit_cutoffs':3,'independent_forward_probability_cells':3873,
        'native_orders':orders,'native_hourly_nav_points':hours,'native_daily_budget_checks':budgets,
        'maximum_nav_error_usdt':max(a['marked']['maximum_nav_error_usdt']for a in validation['audits']),
        'maximum_funding_error_usdt':max(a['marked']['maximum_funding_error_usdt']for a in validation['audits']),
        'native_callback_errors':0,'all_original_source_and_input_hashes_verified':True,
        'figure_visually_checked':True,'figure_sha256':sha(plot),
        'new_registry_definitions':0,'factor_analysis_calls':0,'Vibe_SQLite_benches':0,
        'new_accepted_strategy_or_factor':0,'independent_oos':False,'actual_private_exchange_settlement_certified':False,
        'economic_gate_pass':False,'objective_achieved':False,'orders_enabled':False,'deployment':False,'services_changed':False}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
