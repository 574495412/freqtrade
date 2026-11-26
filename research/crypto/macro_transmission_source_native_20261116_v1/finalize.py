"""Verify input checkpoint and prior evidence; never launch a financial account."""
import hashlib
import json
from pathlib import Path

from bootstrap import boot

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,value):(HERE/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def main():
    native=boot()
    assert native['original_native_files']==595
    sources=read(HERE/'source_receipts.json')
    http=read(HERE/'macro_http_receipts.json')
    assert len(sources)==5 and len(http)==2
    for row in sources+http:
        assert row['status']==200 and sha(row['path'])==row['sha256']
    source=read(HERE/'source_witnesses.json')
    assert source['passed'] and source['source_fixture_rows']==2722
    assert source['absent_target_calendar_dates']==77
    assert source['source_bh_compounds_log_returns_as_simple']
    assert source['true_buy_and_hold_multiple']==1 and .5195<source['source_formula_multiple']<.5196
    inputs=read(HERE/'input_audit.json')
    assert inputs['passed'] and inputs['total_raw_rows']==969 and inputs['total_original_null_rows']==93
    assert inputs['native_subclass'] and inputs['new_funded_accounts']==0
    modes=inputs['mode_audits']
    assert modes[0]['unavailable']==14592 and modes[1]['known_or_model_only']==14592
    assert all(not row['historical_first_seen_certified'] for row in modes)
    assert all(row['actual_snapshot_observed_before_decision_clocks']==0 for row in modes)
    for row in read(HERE/'workflow_ownership.json')['owner_files']+read(HERE/'workflow_ownership.json')['macro_owner_files']:
        assert sha(row['path'])==row['sha256']
    assert '34 passed' in (HERE/'tests_final.log').read_text()
    assert 'All checks passed!' in (HERE/'lint_final.log').read_text()
    assert 'All checks passed!' in (HERE/'lint_research_final.log').read_text()
    assert 'already formatted' in (HERE/'format_final.log').read_text()
    parents=[]
    for relative,digest,count in [
        ('research/crypto/mama_phase_source_native_20261116_v1',
         '2daa244bef880cc3240319495dda985a4551de6d3829de98ea657538403a9bdf',173),
        ('research/equity/short_position_admission_native_20261116_v1',
         'ea36ad8a4f2a662cc7d76406cf16a9437ea62db393d1f285d5bf82a9405bd6c8',31),
    ]:
        file=ROOT/relative/'artifact_manifest.json'
        assert sha(file)==digest
        rows=read(file)['files'];assert len(rows)==count
        for row in rows:assert sha(row['path'])==row['sha256'],row['path']
        parents.append(dict(path=str(file),sha256=digest,verified_paths=len(rows)))
    save('verification.json',dict(passed=True,evidence_tier='source and native input technical only',
        native_engine=native,new_unique_checks=34,original_macro_raw_rows=969,
        original_nulls_preserved=93,source_repository_files=5,macro_HTTP_payloads=2,
        actual_native_callback_input_checks=29184,independent_economic_samples=0,
        default_mode_historical_unavailable_hours=14592,model_only_hours=14592,
        historical_first_seen_certified=False,source_return_math_admitted=False,
        actual_new_financial_accounts=0,new_portfolios=0,new_registry_definitions=0,
        factor_analysis=0,bench=0,accepted_independent_alpha=0,net_return=None,cagr=None,drawdown=None,
        source_financial_engine_executed=False,independent_oos=False,production_eligible=False,
        orders_enabled=False,services_changed=False,parent_seals=parents))
    save('goal_audit.json',dict(previous_goal_turn='progress:four completed rejected native MAMAaccounts and independent cash/fill audits',
        current_goal_turn='progress:distinct macro source reviewed;financial formula counterexample;969 raw source rows andnative input extension with34checks',
        goal_status='active',goal_complete=False,higher_comparable_return_established=False,
        remaining='source/account method admission;explicit execution/data clock contract;native complete funds/portfolio evidence;independent validation',
        source_account_reproduction='not admitted',new_accepted_independent_alpha=0,
        no_parameter_rescue=True,no_orders_or_service_changes=True))
    excluded={'artifact_manifest.json','checkpoint_receipt.json'}
    paths={p for p in HERE.rglob('*') if p.is_file() and p.name not in excluded
           and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    paths.update(ROOT/'freqtrade/data'/name for name in [
        'macro_transmission.py','macro_transmission_provider.py','macro_transmission.md'])
    rows=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(paths)]
    save('artifact_manifest.json',dict(scope='post-result source and input checkpoint;not financial preregistration or return evidence',files=rows))
    for row in rows:assert sha(row['path'])==row['sha256']
    receipt=dict(manifest_sha256=sha(HERE/'artifact_manifest.json'),verified_paths=len(rows),
        new_financial_accounts=0,goal_complete=False)
    save('checkpoint_receipt.json',receipt);print(json.dumps(receipt))


if __name__=='__main__':main()
