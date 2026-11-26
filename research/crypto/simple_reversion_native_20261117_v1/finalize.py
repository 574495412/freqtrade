"""Seal full native account evidence and preserve the failed observation attempt."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).absolute().parent


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')


def main():
    assert not(HERE/'artifact_manifest.json').exists()
    plan=read(HERE/'plan_v2.json');x=read(HERE/'results.json');out=Path(plan['output_root'])
    for name in ['implementation_freeze.json','analysis_freeze.json','revision_freeze.json']:
        for path,digest in read(HERE/name).items():assert sha(path)==digest,path
    assert read(HERE/'causal_checks.json')['passed']and read(HERE/'capture_validation.json')['passed']
    assert read(HERE/'execution_review.json')['passed']and read(HERE/'endpoint_components.json')['passed']
    assert 'All checks passed!'in(HERE/'final_lint.log').read_text()
    assert x['new_native_wallets']==4 and x['new_native_trades']==1000 and x['new_simulated_fills']==2000 and x['new_hour_points']==58368
    assert not x['financial_gate_passed']and not any(r['frozen_financial_gate']for r in x['portfolios'])
    source=HERE/'source/SimpleMeanReversion.py'
    for row in read(HERE/'source_receipts.json')['records']:
        raw=Path(row['local_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob']
    for c in plan['candidates']:
        d=out/(c['name']+f"_{c['cost_bps']}bps")
        assert sha(d/'strategies/SimpleMeanReversion.py')==sha(source)
        assert read(d/'process_receipt.json')['returncode']==0 and read(d/'independent_account_audit.json')['passed']
        assert not(d/'lossless_cache').exists()
        assert read(d/'independent_account_audit.json')['open_trades_at_common_endpoint']==0
    old=Path(read(HERE/'plan.json')['output_root'])/'reversion_1000_conservative_20bps'
    assert read(old/'process_receipt.json')['returncode']!=0 and read(old/'failure.json')['type']=='KeyError'
    assert not list((old/'native_results').glob('*.zip'))
    assert (HERE/'analyze.py').read_text().replace('plan = read(HERE / "plan.json")','plan = read(HERE / "plan_v2.json")')==(HERE/'analyze_v2.py').read_text()
    assert read(HERE/'figure_review.json')['image_sha256']==sha(HERE/'account_curves.png')
    parent_files=0
    for path,digest in read(HERE/'protocol.json')['inputs'].items():
        assert sha(path)==digest
        for r in read(path)['files']:assert sha(r['path'])==r['sha256'];parent_files+=1
    verification=dict(verified_at=datetime.now(UTC).isoformat(),passed=True,source_commit='16aa652f9db446e3738fea030e33ecc76be1e3ce',original_strategy_bytes_unchanged=True,past_only_checks=6,native_roi_semantics_verified=True,complete_native_wallets=4,failed_receipt_phase_attempts_preserved=1,native_trades=1000,simulated_fills=2000,common_hour_points=58368,maximum_independent_nav_error_usdt=x['maximum_independent_nav_error_usdt'],all_terminal_accounts_flat=True,all_frozen_portfolios_rejected=True,financial_gate_passed=False,parent_sealed_files_unchanged=parent_files,independent_oos=False,historical_venue_rules_certified=False,orders_enabled=False,services_changed=False,production_eligible=False,goal_complete=False)
    save(HERE/'verification.json',verification)
    files=[p for p in HERE.rglob('*')if p.is_file()and'upstream.git'not in p.relative_to(HERE).parts and p.name!='artifact_manifest.json']
    # Include original failed attempt, native initialization and completed v2 workers.
    runtime=Path(read(HERE/'plan.json')['output_root']);files += [p for p in runtime.rglob('*')if p.is_file()]
    records=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))for p in sorted(set(files))]
    save(HERE/'artifact_manifest.json',dict(created_at=datetime.now(UTC).isoformat(),files=records,source_git_object_database_excluded=True,orders_enabled=False))
    for r in read(HERE/'artifact_manifest.json')['files']:assert sha(r['path'])==r['sha256']
    print(json.dumps(dict(passed=True,sealed_files=len(records),manifest_sha256=sha(HERE/'artifact_manifest.json'),native_wallets=4,trades=1000,fills=2000,financial_gate_passed=False)))


if __name__=='__main__':main()
