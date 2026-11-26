"""Recheck immutable research files, actual native ZIP contracts and run receipts."""
import json
from pathlib import Path
from .prepare import HERE,OUT,sha
from .seal import current
from .native_runner import verify
from research.crypto.band_trend_native_20261113_v1.analyze import read_native

def main():
    manifest=json.loads((HERE/'artifact_manifest.json').read_text());assert manifest['file_count']==len(manifest['files'])
    assert {r['path']for r in manifest['files']}=={str(p)for p in current()}
    for r in manifest['files']:
        p=Path(r['path']);assert p.is_file()and not p.is_symlink()and p.stat().st_size==r['bytes']and sha(p)==r['sha256'],str(p)
    plan=json.loads((HERE/'plan.json').read_text());v=json.loads((HERE/'verification.json').read_text());comparison=json.loads((HERE/'comparison.json').read_text())
    assert v['passed_within_scope']and v['actual_successful_native_accounts']==6 and v['actual_hourly_points']==87552
    assert len(v['numeric_gates'])==4 and all(not r['passed']for r in v['numeric_gates'])
    count=0
    for candidate in plan['candidates']:
        verify(plan,candidate)
        for cost in plan['total_cost_bps_per_side']:
            path=OUT/f'{candidate["name"]}_{cost}bps';receipt=json.loads((path/'process_receipt.json').read_text())
            assert receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json')
            stats,archive,config=read_native(path,candidate['strategy']);assert stats['dry_run_wallet']==1000 and stats['timeframe']=='1m' and config['fee']==cost/10000
            proof=next(x for x in v['evidence']if x['account']==path.name);assert sha(archive)==proof['native_zip_sha256']
            actual=json.loads((path/'independent_audit.json').read_text());assert actual['cash']['passed'] and actual['policy']['passed'] and actual['hourly']['passed'] and actual['funding']['passed_native_formula']
            assert actual['hourly']['points']==14592 and json.loads((path/'callback_errors.json').read_text())['passed']
            assert all(not t['is_short']and t['leverage']==1 for t in stats['trades']);count+=1
    assert count==6 and len(comparison['accounts'])==6
    receipt=json.loads((HERE/'completion_receipt.json').read_text());assert not receipt['goal_achieved'] and receipt['goal_status']=='active' and not receipt['orders_enabled']
    assert receipt['verification_sha256']==sha(HERE/'verification.json')and receipt['report_sha256']==sha(HERE/'README.md')
    result={'passed':True,'manifest_paths':len(manifest['files']),'verified_native_accounts':count,'full_hourly_points':87552,'new_accepted_alpha':0,'goal_achieved':False,'goal_status':'active','no_orders_or_services_changed':True}
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
