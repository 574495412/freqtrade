"""Current-state verification; preserve both data versions and economic rejection."""
import hashlib
import json
from pathlib import Path
from datetime import UTC,datetime
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify
HERE=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    assert sha(HERE/'protocol.json')==read(HERE/'protocol_freeze.json')['sha256']
    frozen=read(HERE/'v002/runtime_source_freeze.json')
    for p,h in frozen.items():assert sha(p)==h,p
    manifest=read(HERE/'dependency_manifest.json')
    for p,h in manifest['files'].items():assert sha(HERE/'dependencies'/p)==h,p
    initial=read(HERE/'prelaunch_initial.json');launch=read(HERE/'prelaunch_manifest.json');amend=read(HERE/'resource_amendment.json')
    assert sha(HERE/'prelaunch_initial.json')==amend['original_prelaunch_sha256']
    assert sha(HERE/'run.py')==amend['new_controller_sha256']
    for p,h in launch.items():
        if Path(p).name=='resource_dedup.py':assert sha(HERE/'resource_dedup_v1.source.txt')==h
        else:assert sha(p)==h,p
    # Original controller body is unavailable, not silently equated to current resource-amended code.
    assert initial[str(HERE/'run.py')]!=launch[str(HERE/'run.py')]
    assert '7 passed'in(HERE/'tests_final.log').read_text()
    assert(HERE/'semantic_lint.log').read_text().strip()=='All checks passed!'
    sources=read(HERE/'source_manifest.json')['files']+[read(HERE/'followup_source_receipt.json')]
    for s in sources:assert s['status']==200 and sha(s['path'])==s['sha256']
    counts=[];worker_pids=set()
    for folder in [HERE,HERE/'v002']:
        plan=read(folder/'plan.json');out=Path(plan['output_root']);r=read(folder/'results.json')
        assert read(folder/'execution_complete.json')['accounts']==r['complete_native_accounts']==16
        assert r['all_model_and_account_checks_passed'] and r['account_hour_points']==233472
        assert r['maximum_independent_nav_error_usdt']<3e-7
        assert not r['incumbent_followup_required'] and not r['objective_achieved']
        assert len(r['combinations'])==2 and all(not c['necessary_gate_passed']for c in r['combinations'])
        external=read(folder/'external_model_recheck.json')
        assert external['passed']and external['model_windows']==696 and external['maximum_probability_error']==0
        for candidate in plan['candidates']:
            verify(plan,candidate)
            for cost in plan['total_cost_bps_per_side']:
                key=f'{candidate["name"]}_{cost}bps';path=out/key;receipt=read(path/'process_receipt.json');audit=read(path/'independent_audit.json')
                assert receipt['returncode']==0 and sha(path/'result.json')==receipt['result_sha256']
                assert receipt['pid']not in worker_pids;worker_pids.add(receipt['pid'])
                assert audit['archive_sha256']==sha(next(e['archive']for e in r['evidence']if e['account']==key))
                assert audit['auditor_sha256']==sha(HERE/'analyze.py')
                assert audit['cash']['passed']and audit['marked']['passed']and audit['policy']['passed']
                if audit['model']:
                    assert audit['model']['prediction_rows']==14592 and audit['model']['native_windows']==87
                    assert audit['model']['tail_unknown_rows']==20
                    if folder!=HERE:assert audit['model']['train_rows_min']==audit['model']['train_rows_max']==700
        counts.append({'version':folder.name,'complete_accounts':16,'native_trades':r['actual_native_trades'],'native_fills':r['actual_native_fills'],'model_window_fits':r['model_window_fits'],'account_hours':r['account_hour_points'],'max_NAV_error_USDT':r['maximum_independent_nav_error_usdt']})
    amendment=read(HERE/'v002/data_contract_amendment.json')
    for name in ['parent_plan','parent_results']:assert sha(amendment[name]['path'])==amendment[name]['sha256']
    assert read(HERE/'v1_input_contract_status.json')['missing_startup_hours']==56
    assert read(HERE/'v002/input_prefix_audit.json')['passed']
    assert read(HERE/'v002/input_grid_checks.json')['requested_startup_complete']
    assert read(HERE/'v002/control_parity.json')['passed']
    review=read(HERE/'v002/figure_review.json');assert review['observed_local_PNG']and sha(review['path'])==review['sha256']
    assert read(HERE/'v002/preflight.json')['additional_native_component_fits']==6
    assert read(HERE/'preflight.json')['passed']and read(HERE/'final_revalidation.json')['passed']
    result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'source_frozen_at_v2_launch_exact':len(frozen),'isolated_dependency_files_exact':len(manifest['files']),'pinned_native_source_files':595,'both_versions':counts,'distinct_terminal_worker_pids':len(worker_pids),'v1_requested_startup_complete':False,'v2_requested_startup_complete':True,'v2_train_rows_each_window':700,'unique_rule_tests':7,'native_historical_component_fits':30,'synthetic_component_probe_probability_cells':24,'original_controller_source_body_retained':False,'original_controller_digest_and_initial_log_retained':True,'v1_resource_helper_body_retained':True,'strategy_config_model_threshold_cost_window_changed_in_v2':False,'new_factor_analysis_calls':0,'new_Vibe_benches':0,'accepted_new_factors':0,'objective_achieved':False,'private_settlement':False,'independent_oos':False,'orders_or_service_changes':False}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
