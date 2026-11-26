"""Verify complete native scenario evidence and seal it; never launch an account."""
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).absolute().parent


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):(HERE/name).write_text(json.dumps(value,indent=2)+'\n')


def main():
    frozen=read(HERE/'implementation_freeze.json');assert len(frozen)==32
    for path,digest in frozen.items():assert sha(path)==digest,path
    protocol=read(HERE/'protocol_freeze.json');assert sha(protocol['path'])==protocol['sha256']
    plan=read(HERE/'plan.json');engine=read(plan['engine_manifest']['path'])
    for path,digest in engine['files'].items():assert sha(Path(plan['engine_source'])/path)==digest
    parents=[]
    for row in read(HERE/'parent_seals_before.json'):
        assert sha(row['path'])==row['sha256']
        files=read(row['path'])['files']
        for file in files:assert sha(file['path'])==file['sha256'],file['path']
        parents.append(dict(**row,verified_paths=len(files)))
    assert len(parents)==6
    receipts=[];peaks=[]
    for c in plan['candidates']:
        directory=Path(plan['output_root'])/(c['name']+f"_{c['cost_bps']}bps")
        receipt=read(directory/'process_receipt.json')
        assert receipt['returncode']==0 and sha(directory/'result.json')==receipt['result_sha256']
        assert read(directory/'independent_account_audit.json')['passed']
        peaks.append(read(directory/'progress.json')['maximum_rss_bytes']);receipts.append(receipt)
    assert len(receipts)==12 and max(peaks)<plan['memory_guard_bytes']
    for a,b in zip(receipts[:-1],receipts[1:],strict=True):
        assert datetime.fromisoformat(a['finished_at'])<=datetime.fromisoformat(b['started_at'])
    first=datetime.fromisoformat(receipts[0]['started_at'])
    assert (HERE/'implementation_freeze.json').stat().st_mtime<first.timestamp()
    assert datetime.fromisoformat(read(HERE/'protocol.json')['frozen_at'])<first
    assert read(HERE/'execution_complete.json')['accounts']==12
    results=read(HERE/'results.json');budgets=read(HERE/'execution_audit.json')
    wallets=read(HERE/'native_wallet_audit.json');tracking=read(HERE/'target_tracking.json')
    assert budgets['passed'] and wallets['passed'] and tracking['passed']
    assert budgets['actual_budget_clocks']==4876 and budgets['independent_target_quantities']==9752
    assert budgets['actual_natural_closes']==0 and budgets['separate_force_exits']==12
    assert wallets['actual_native_balance_rows']==350196 and wallets['complete_target_quote_cash_clocks']==175104
    assert results['new_native_wallets']==12 and results['new_native_trades']==12
    assert results['new_simulated_fills']==324 and results['new_hour_points']==175104
    assert len(results['portfolios'])==6 and len(results['standalone_gates'])==4
    assert not any(r['frozen_financial_gate'] for r in results['portfolios']+results['standalone_gates'])
    components=read(HERE/'endpoint_components.json')
    assert sum(r['common_endpoint_actual_fills'] for r in components)==312
    assert sum(r['actual_force_exit_fills_excluded'] for r in components)==12
    assert len(tracking['full_hold_actual_financial_parity'])==4
    assert read(HERE/'policy_audit.json')['actual_policy_rows']==1219
    assert read(HERE/'preflight.json')['native_converted_hour_rows']==87552
    assert read(HERE/'native_document_receipt.json')['same']
    assert '3 passed' in (HERE/'tests_initial.log').read_text()
    assert 'All checks passed!' in (HERE/'lint_final.log').read_text()
    save('execution_review.json',dict(financial_attempts=12,financial_failures=0,financial_replays=0,
        all_exit_zero=True,one_financial_child_at_a_time=True,process_receipts=receipts,
        maximum_sampled_rss_bytes=max(peaks),sampling_interval_seconds=.25,
        first_native_start=first.isoformat(),producer_freeze_mtime_utc=
        datetime.fromtimestamp((HERE/'implementation_freeze.json').stat().st_mtime,UTC).isoformat()))
    save('verification.json',dict(passed=True,evidence_tier='conditional historical native account model',
        original_engine_files=len(engine['files']),producer_freeze_paths=len(frozen),parent_seals=parents,
        actual_native_wallets=12,actual_native_Trade_lifecycles=12,naturally_completed_lifecycles=0,
        actual_simulated_fills=324,common_endpoint_fills=312,separate_terminal_force_exits=12,
        complete_hourly_nav_points=175104,maximum_independent_nav_error_usdt=results['maximum_independent_nav_error_usdt'],
        actual_native_wallet_rows=350196,complete_budget_clocks=4876,independent_target_quantity_checks=9752,
        actual_full_hold_financial_parities=4,new_actual_quote_prefix_checks=3,reused_input_checks=34,
        financial_gate_passes=0,actual500plus500_portfolios=6,historical_macro_firstseen_certified=False,
        default_historical_input_rejection_unchanged=True,source_author_account_reproduced=False,
        independent_oos=False,new_accepted_independent_alpha=0,new_registry_definitions=0,
        new_factor_analysis=0,new_vibe_bench=0,private_settlement_certified=False,
        full_historical_venue_rules_certified=False,production_eligible=False,orders_enabled=False,services_changed=False))
    save('goal_audit.json',dict(previous_turn='progress:macro source counterexample,969rawrows,native input and34checks',
        current_turn='progress:12complete native conditional accounts and6fixed actual500portfolios,allmacro economic gates fail',
        higher_comparable_net_return_established=False,new_accepted_independent_alpha=0,
        source_clock_proof=False,goal_status='active',goal_complete=False,
        no_parameter_rescue=True,no_orders_or_service_changes=True))
    excluded={'artifact_manifest.json','checkpoint_receipt.json'}
    paths={p for base in [HERE,Path(plan['output_root'])] for p in base.rglob('*') if p.is_file()
        and p.name not in excluded and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    rows=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(paths)]
    save('artifact_manifest.json',dict(scope='post-result rejected conditional macro account study;not preregistration or first-seen certification',files=rows))
    for row in rows:assert sha(row['path'])==row['sha256']
    receipt=dict(manifest_sha256=sha(HERE/'artifact_manifest.json'),verified_paths=len(rows),actual_native_wallets=12,goal_complete=False)
    save('checkpoint_receipt.json',receipt);print(json.dumps(receipt))


if __name__=='__main__':main()
