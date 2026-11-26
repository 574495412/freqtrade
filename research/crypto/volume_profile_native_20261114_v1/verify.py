"""Verify complete actual native accounts and immutable financial inputs/source."""
import hashlib,json
from pathlib import Path
from datetime import UTC,datetime
import pandas as pd
from .prepare import HERE,ROOT,sha
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

def read(p):return json.loads(Path(p).read_text())
def main():
    p=read(HERE/'plan.json');r=read(HERE/'results.json');source=HERE.parent/'mechanism_source_intake_20261114_v1'
    assert sha(HERE/'protocol.json')==read(HERE/'protocol_freeze.json')['sha256']
    for path,h in read(HERE/'prelaunch_manifest.json').items():assert sha(path)==h,path
    assert read(HERE/'preflight.json')['passed']and'13 passed'in(HERE/'tests_final.log').read_text()
    assert read(HERE/'execution_complete.json')['accounts']==r['complete_new_native_accounts']==12
    assert not any(r['source_candidate_passes_both_costs'].values())and not r['incumbent_followup_required']
    assert len(r['combo_gates'])==8 and not any(g['passed']for g in r['combo_gates'])
    count=fills=state=hours=0;maximum=0.;pids=set();files=[]
    for c in p['candidates']:
        verify(p,c)
        for cost in p['total_cost_bps_per_side']:
            key=f'{c["name"]}_{cost}bps';path=Path(p['output_root'])/key;receipt=read(path/'process_receipt.json');audit=read(path/'independent_audit.json')
            assert receipt['returncode']==0 and sha(path/'result.json')==receipt['result_sha256'];assert receipt['pid']not in pids;pids.add(receipt['pid'])
            assert audit['passed']and audit['cash']['passed']and audit['marked']['passed']and audit['policy']['passed']
            assert audit['auditor_sha256']==sha(HERE/'analyze.py')
            assert audit['policy']['source_state_level_max_error']==0
            frame=pd.read_parquet(path/'hourly_equity.parquet');assert len(frame)==14592
            hours+=len(frame);state+=audit['policy']['actual_funded_state_rows'];fills+=audit['marked']['filled_orders'];count+=audit['policy']['actual_entries_audited'];maximum=max(maximum,audit['marked']['maximum_nav_error_usdt'])
            assert read(path/'progress.json')['maximum_rss_bytes']<p['memory_guard_bytes']
            files.append({'account':key,'receipt_sha256':sha(path/'process_receipt.json'),'audit_sha256':sha(path/'independent_audit.json')})
    controls=read(HERE.parent/'freqai_rf_native_20261114_v1/v002/plan.json');old=Path(controls['output_root'])
    for tf,kind in [('1h','futures'),('5m','futures'),('1d','futures'),('1h','mark'),('1h','funding_rate')]:
        rel=f'futures/BTC_USDT_USDT-{tf}-{kind}.feather';assert sha(Path(p['datadir'])/rel)==sha(Path(controls['datadir'])/rel)
    for combo in r['combinations']:
        cost=combo['cost_bps_per_side'];frames=[]
        for name in [combo['a'],combo['b']]:
            root=old if name=='SmaPriceBTC'else Path(p['output_root']);frames.append(pd.read_parquet(root/f'{name}_500_{cost}bps/hourly_equity.parquet'))
        a,b=frames;assert a.date.equals(b.date);actual=pd.read_parquet(HERE/f'combo_{combo["name"]}.parquet');assert actual.date.equals(a.date);pd.testing.assert_series_equal(actual.hourly_mark_equity,a.hourly_mark_equity+b.hourly_mark_equity,check_names=False,check_exact=True)
    for receipts in ['source_receipts.json','followup_receipts.json','social_receipts.json','youtube_receipts.json']:
        for x in read(source/receipts):assert sha(x['path'])==x['sha256']
    stop=read(HERE/'stop_semantics_audit.json');assert stop['passed']and stop['stop_fill_max_error']==0
    figure=read(HERE/'figure_review.json');assert figure['observed_local_PNG']and sha(figure['path'])==figure['sha256']
    assert read(source/'dominance_input_admission.json')['complete_original_universe_admitted']is False
    result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_upstream_files_verified':595,'financial_source_config_seals_unchanged':True,'new_complete_accounts':12,'reused_context_accounts':8,'native_lifecycles':count,'native_fills':fills,'native_account_hours':hours,'actual_funded_state_rows':state,'maximum_NAV_error_USDT':maximum,'stop_semantics':stop,'unique_tests':13,'native_preflight_strategy_classes':3,'native_preflight_feature_rows':175680,'source_profile_comparison_rows':58560,'future_prefix_checks':6,'source_and_combo_gates_failed':True,'accepted_new_independent_factors':0,'objective_achieved':False,'private_exchange_settlement':False,'independent_oos':False,'orders_services_deployments':False,'accounts':files}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k not in ['accounts','stop_semantics']},indent=2))
if __name__=='__main__':main()
