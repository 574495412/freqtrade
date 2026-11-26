"""Seal completed research evidence; never starts or changes an account."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).absolute().parent


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')


def main():
    assert not (HERE/'artifact_manifest.json').exists(),'Never replace a sealed artifact'
    plan=read(HERE/'plan.json');result=read(HERE/'results.json');checks=[]
    for name in ['implementation_freeze.json','analysis_freeze.json']:
        for path,digest in read(HERE/name).items():assert sha(path)==digest,path
        checks.append(name)
    revision=read(HERE/'analysis_revision.json')
    assert sha(HERE/'analyze.py')==revision['original_sha256']and sha(HERE/'analyze_v2.py')==revision['revised_sha256']
    original=(HERE/'analyze.py').read_text().split('    up = ',1)[1]
    assert sha_bytes(original.encode())==revision['comparison_code_sha256']
    assert original==(HERE/'analyze_v2.py').read_text().split('    up = ',1)[1]
    receipts=read(HERE/'selected_source_receipts.json')
    for r in receipts['records']:
        raw=Path(r['local_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==r['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==r['git_blob']
    assert read(HERE/'input_review.json')['passed']and read(HERE/'budget_audit.json')['passed']
    assert read(HERE/'endpoint_components.json')['passed']and read(HERE/'execution_review.json')['passed']
    assert result['new_native_wallets']==4 and result['new_simulated_fills']==944 and result['new_hour_points']==58368
    assert not result['financial_gate_passed']and not result['production_eligible']
    assert not any(r['frozen_financial_gate']for r in result['portfolios'])
    assert '6 passed' in (HERE/'tests_validated.log').read_text()
    assert 'All checks passed!'in(HERE/'final_lint.log').read_text()
    out=Path(plan['output_root'])
    for c in plan['candidates']:
        d=out/(c['name']+f"_{c['cost_bps']}bps")
        assert read(d/'process_receipt.json')['returncode']==0
        assert read(d/'independent_account_audit.json')['passed']
        assert not (d/'lossless_cache').exists()
    verification=dict(passed=True,verified_at=datetime.now(UTC).isoformat(),frozen_source_and_implementation_unchanged=True,analysis_schema_fix_only=True,source_git_blobs_verified=len(receipts['records']),tests=6,native_wallets=4,completed_native_processes=4,independent_nav_maximum_error_usdt=result['maximum_independent_nav_error_usdt'],new_factor_analysis=0,new_vibe_bench=0,source_default_oi_reproduced=False,independent_oos=False,historical_venue_rules_certified=False,higher_return_found_within_frozen_spot_model=True,financial_gate_passed=False,orders_enabled=False,services_changed=False,goal_complete=False)
    save(HERE/'verification.json',verification)
    paths=[p for p in HERE.rglob('*')if p.is_file()and 'upstream.git'not in p.relative_to(HERE).parts and p.name!='artifact_manifest.json']
    paths += [p for p in out.rglob('*')if p.is_file()]
    paths += [Path(r['path'])for c in plan['candidates']for r in c['source_files'].values()]
    files=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))for p in sorted(set(paths))]
    manifest=dict(created_at=datetime.now(UTC).isoformat(),files=files,clone_object_database_excluded=True,selected_sources_gitverified=True,orders_enabled=False)
    save(HERE/'artifact_manifest.json',manifest)
    for r in read(HERE/'artifact_manifest.json')['files']:assert sha(r['path'])==r['sha256'],r['path']
    print(json.dumps(dict(passed=True,sealed_files=len(files),manifest_sha256=sha(HERE/'artifact_manifest.json'),maximum_nav_error_usdt=result['maximum_independent_nav_error_usdt'],higher_model_return=True,financial_gate_passed=False,orders_enabled=False)))


def sha_bytes(raw):return hashlib.sha256(raw).hexdigest()


if __name__=='__main__':main()
