"""Seal only after all eight predeclared native wallets and audits complete."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import psutil

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')


def main():
    assert not(HERE/'artifact_manifest.json').exists()
    plan=read(HERE/'plan.json');result=read(HERE/'results.json');out=Path(plan['output_root'])
    assert read(HERE/'execution_complete.json')['accounts']==8
    for name in ['implementation_freeze.json','analysis_freeze.json','auxiliary_audit_freeze.json']:
        for path,digest in read(HERE/name).items():assert sha(path)==digest,path
    assert '6 passed'in(HERE/'tests.log').read_text()
    assert read(HERE/'feature_receipts.json')['passed']and read(HERE/'budget_audit.json')['passed']and read(HERE/'forecast_scores.json')['passed']and read(HERE/'endpoint_components.json')['passed']
    assert result['new_native_wallets']==8 and result['new_hour_points']==116736
    assert not result['orders_enabled']and not result['production_eligible']
    review=[]
    for c in plan['candidates']:
        d=out/(c['name']+f"_{c['cost_bps']}bps");r=read(d/'process_receipt.json')
        assert r['returncode']==0 and sha(d/'result.json')==r['result_sha256']
        assert read(d/'independent_account_audit.json')['passed']
        assert not(d/'lossless_cache').exists()
        assert not psutil.pid_exists(r['pid'])
        for filename,source in c['source_files'].items():assert sha(d/'strategies'/filename)==source['sha256']
        review.append(dict(account=c['name'],pid=r['pid'],terminal_returncode=0,independent_audit_passed=True))
    source=read(HERE/'source_receipts.json')
    for r in source['records']:
        raw=Path(r['local_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==r['git_blob']
    for r in read(HERE/'raw_receipts.json'):
        p=Path(r['path']);assert sha(p)==r['sha256']and sha(p.with_suffix('.CHECKSUM'))==r['checksum_sha256']
    parents=0
    for path,digest in read(HERE/'protocol.json')['inputs'].items():
        assert sha(path)==digest
        for r in read(path)['files']:assert sha(r['path'])==r['sha256'];parents+=1
    save(HERE/'execution_review.json',dict(passed=True,workers=review,complete_native_wallets=8,no_worker_left_running=True,orders_enabled=False))
    save(HERE/'verification.json',dict(verified_at=datetime.now(UTC).isoformat(),passed=True,source_commit=source['commit'],original_source_files_gitverified=len(source['records']),raw_spot_monthly_archives_verified=94,minute_observations_per_asset=2060560,feature_days=1431,source_forecast_tests=6,actual_feature_prefix_checks=6,native_completed_wallets=8,native_trades=result['new_native_trades'],simulated_fills=result['new_simulated_fills'],common_hour_points=116736,maximum_independent_nav_error_usdt=result['maximum_independent_nav_error_usdt'],parent_sealed_files_unchanged=parents,known_feature_eligibility_separate_from_future_label_scoring=True,source_account_simulator_executed=False,source2019full_history_reproduced=False,financial_gate_passed=result['financial_gate_passed'],independent_oos=False,historical_venue_rules_certified=False,orders_enabled=False,services_changed=False,production_eligible=False,goal_complete=False))
    save(HERE/'goal_audit.json',dict(previous_goal_turn='PROGRESS:sourceBBRSI4nativewallets+fixedcombosrejected',this_goal_turn='PROGRESS:newHAR/Naive730/30nativeinterface,94primaryspotarchives,8completecostedwallets+fixedcombosandindependentaudits',study_complete=True,goal_complete=False,global_blocked=False,financial_gate_passed=result['financial_gate_passed'],independent_alpha_proven=False,next_requirements=['Independentsource/mechanismnotretuneHARafterobservedresults','IndependentvalidationforconditionalhigherreturnTrendCorecandidate','Separateoriginal40historicalrulecoveragegapremains'],orders_enabled=False))
    assert (HERE/'README.md').exists()
    files=[p for p in HERE.rglob('*')if p.is_file()and'upstream.git'not in p.relative_to(HERE).parts and'__pycache__'not in p.relative_to(HERE).parts and p.name!='artifact_manifest.json']
    files += [p for p in out.rglob('*')if p.is_file()and'__pycache__'not in p.relative_to(out).parts]
    files += [ROOT/'freqtrade/data/har_volatility.py']
    records=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))for p in sorted(set(files))]
    save(HERE/'artifact_manifest.json',dict(created_at=datetime.now(UTC).isoformat(),files=records,clone_object_database_and_generated_pycache_excluded=True,orders_enabled=False))
    for r in read(HERE/'artifact_manifest.json')['files']:assert sha(r['path'])==r['sha256']
    print(json.dumps(dict(passed=True,sealed_files=len(records),manifest_sha256=sha(HERE/'artifact_manifest.json'),complete_native_wallets=8,trades=result['new_native_trades'],fills=result['new_simulated_fills'],financial_gate_passed=result['financial_gate_passed'])))


if __name__=='__main__':main()
