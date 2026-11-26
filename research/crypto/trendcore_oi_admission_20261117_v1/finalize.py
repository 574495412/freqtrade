"""Seal a rejected data intake without claiming a backtest or new alpha."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')


def main():
    assert not (HERE/'artifact_manifest.json').exists()
    x=read(HERE/'reconciliation.json');d=read(HERE/'dependency_scope.json')
    assert x['raw_integrity_passed']and not x['strict_source_input_admitted']
    assert x['verified_daily_archives']==974 and x['raw_rows']==280382
    assert x['normal2355_matches']==965 and len(x['short_end_days'])==1 and len(x['next_midnight_matches'])==7 and len(x['unresolved'])==1
    assert x['unresolved'][0]['day']=='2024-03-11'
    assert d['possible_fullwindow_execution_clocks']==280 and d['last_possible_clock']=='2025-03-12 00:00:00+00:00'
    assert d['later_account_state_can_remain_affected_after_direct_indicator_dependency']
    assert not (ROOT/'freqtrade/data/trendcore_oi_signals.py').exists()
    assert not (HERE/'plan.json').exists()and not (HERE/'input_admission.json').exists()
    receipts=read(HERE/'source_receipts.json')
    for r in receipts['records']:
        raw=Path(r['local_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==r['git_blob']
    parent_files=0
    for path,digest in read(HERE/'protocol.json')['inputs'].items():
        assert sha(path)==digest
        for r in read(path)['files']:assert sha(r['path'])==r['sha256'];parent_files+=1
    r=read(HERE/'capture_result.json');assert r['passed']and r['captured']==362
    assert 'All checks passed!'in(HERE/'lint.log').read_text()
    verification=dict(verified_at=datetime.now(UTC).isoformat(),verification_completed=True,raw_integrity_passed=True,source_input_admitted=False,default_oi_backtest_completed=False,source_parquet_git_blob_verified=True,raw_archives_verified=974,new_raw_archives=362,parent_sealed_files_unchanged=parent_files,unverified_source_days=['2024-03-11'],partial_daily_archive_days=['2024-02-16'],next_midnight_source_days=x['next_midnight_matches'],native_financial_accounts=0,new_alpha_claims=0,independent_oos=False,availability_proxy_not_historical_receipt=True,orders_enabled=False,services_changed=False,goal_complete=False)
    save(HERE/'verification.json',verification)
    save(HERE/'goal_audit.json',dict(previous_goal_turn='PROGRESS:native4walletstudysealedwithhigherreturnbutfailedriskgate',this_goal_turn='PROGRESS:362newprimaryarchives,974dayreconciliation,exactclockandunresolvedrecordevidencechangedsourceadmission',current_branch_status='INPUT_NOT_ADMITTED',goal_complete=False,global_blocked=False,next_safe_actions=['Independentsource/versionevidencecouldresolveOIrecord','Othermechanisticallydifferentcryptosourcesremainavailable;noOIretuning'],orders_enabled=False))
    files=[p for p in HERE.rglob('*')if p.is_file()and p.name!='artifact_manifest.json']
    # Reused primary archives stay in place, with both payload and checksum bound.
    for r in read(HERE/'raw_receipts.json'):
        p=Path(r['path']);files.extend([p,p.with_suffix('.CHECKSUM')])
    manifest=dict(created_at=datetime.now(UTC).isoformat(),files=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))for p in sorted(set(files))],source_admission=False,official_native_financial_runs=0,orders_enabled=False)
    save(HERE/'artifact_manifest.json',manifest)
    for r in read(HERE/'artifact_manifest.json')['files']:assert sha(r['path'])==r['sha256']
    print(json.dumps(dict(sealed_files=len(manifest['files']),manifest_sha256=sha(HERE/'artifact_manifest.json'),parent_files_unchanged=parent_files,source_admitted=False,native_financial_runs=0)))


if __name__=='__main__':main()
