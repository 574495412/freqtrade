"""Verify preserved research receipts and bounded historical-data evidence."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
verified={}
def read(relative):return json.loads((ROOT/relative).read_text())
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def check(relative,expected):
    path=ROOT/relative
    if not path.resolve().is_relative_to(ROOT):raise ValueError('Unexpected evidence outside repository')
    actual=sha(path)
    if actual!=expected:raise ValueError('Preserved evidence drift: '+str(relative))
    verified[str(relative)]=actual

freeze=read('research/crypto/rd_program_20261105/freeze.json')
for path,digest in freeze['files'].items():check(path,digest)
# Deployment locks are intentionally not opened: this is a research-only audit.
native=read('research/crypto/rd_program_20261105/p1/native/native_scorecard.json')
for record in native['input_receipts']:check(record['path'],record['sha256'])
official=read('research/crypto/official_factor_20261106/results.json')
for row in official['rows']:
    check(row['archive'],row['archive_sha256']);check(row['receipt'],row['receipt_sha256'])
event=read('research/crypto/rd_program_20261105/data_extension/event_source_audit.json')
check(event['runtime_result']['path'],event['runtime_result']['sha256'])
books=read('research/crypto/rd_program_20261105/data_extension/event_book_replay_result.json')
for row in books['rows']:
    check(row['input_path'],row['input_sha256']);check(row['output']['path'],row['output']['sha256'])
mark=read('research/crypto/rd_program_20261105/data_extension/mark_valuation_result.json')
btc=[]
for row in mark['rows']:
    if row['baseline']!='B1_BTC_HOLD':continue
    check(row['output']['path'],row['output']['sha256'])
    btc.append(dict(id=row['id'],window=row['window'],cost=row['cost_id'],
        net_reference_return=row['official_mark_net_return_conditional_cash'],
        mdd=row['mark_drawdown']['mdd'],rows=row['rows'],output=row['output'],
        same_risk_as_factor=False,valuation='official 5m mark; original frozen conditional cash/funding unchanged',
        historical_execution_certified=False,new_account_runs=0))
result=dict(observed_at_utc=datetime.now(timezone.utc).isoformat(),verified_files=len(verified),hashes=verified,
    original_P1_native_accounts=native['native_account_count'],original_P1_control_accounts=native['control_account_count'],
    original_determinism_checks=native['determinism'],official_accounts_preserved=official['accounts'],
    official_profitable_accounts=official['profitable_accounts'],btc_references=btc,
    historical_samples=[dict(market=r['market'],messages=r['input_messages_processed'],
        exact_local_timestamp_order_passed=r['exact_local_timestamp_order_passed'],
        usable_book_observations=r['usable_book_observations'],
        complete_both_20bps_observations=r['complete_both_20bps_observations']) for r in books['rows']],
    full_historical_window_coverage=False,ordinary_date_access_observed=event['ordinary_date_access'],
    provider_access_refreshed_this_turn=False,private_account_history_verified=False,historical_rules_verified=False,
    new_financial_runs=0,new_causality_diagnostics=0,deployment_files_opened=False)
with (HERE/'prior_evidence_verification.json').open('x') as stream:json.dump(result,stream,indent=2,ensure_ascii=False);stream.write('\n')
print('Verified',len(verified),'preserved files; P1',native['native_account_count']+native['control_account_count'],
    'accounts,',len(native['determinism']),'determinism checks, official',official['accounts'],'accounts; no reruns')
