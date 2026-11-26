"""Seal retained own research artifacts plus explicitly referenced public inputs."""
import json
from datetime import UTC,datetime
from pathlib import Path
from .prepare import HERE,OUT,sha,save
EXCLUDED={'artifact_manifest.json','seal_verification.json'}
def current():
    files=set()
    for base in [HERE,OUT]:
        files.update(p for p in base.rglob('*')if p.is_file()and '__pycache__'not in p.parts and p.name not in EXCLUDED)
    admission=json.loads((HERE/'input_admission.json').read_text())
    for r in admission['archives']:
        files.update([Path(r['archive']['path']),Path(r['checksum']['path'])])
    funding=json.loads((HERE/'funding_input_recheck.json').read_text())
    for r in funding['archive_receipts']:
        files.update([Path(r['path']),Path(r['path']).with_suffix('.CHECKSUM')])
    return sorted(files)
def main():
    assert not(HERE/'artifact_manifest.json').exists()
    v=json.loads((HERE/'verification.json').read_text());assert v['passed_within_scope']and v['actual_successful_native_accounts']==6
    save(HERE/'completion_receipt.json',{'finished_utc':datetime.now(UTC).isoformat(),'status':'COMPLETE_NATIVE_CALENDAR_MATRIX_REJECTED','native_accounts':6,'native_trade_records':sum(x['actual_trades']for x in v['evidence']),'full_hourly_nav_points':v['actual_hourly_points'],'source_exact_replication':False,'new_accepted_alpha':0,'numeric_gates':v['numeric_gates'],'goal_achieved':False,'goal_status':'active','orders_enabled':False,'services_changed':False,'plan_sha256':sha(HERE/'plan.json'),'verification_sha256':sha(HERE/'verification.json'),'report_sha256':sha(HERE/'README.md'),'figure_visually_checked':json.loads((HERE/'visual_review.json').read_text())['passed']})
    records=[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size}for p in current()]
    save(HERE/'artifact_manifest.json',{'sealed_utc':datetime.now(UTC).isoformat(),'scope':'Own completed native study, retained artifacts and referenced official raw candle/funding archives; byte-identical cache hardlinks retain all paths.','files':records,'file_count':len(records),'logical_bytes':sum(r['bytes']for r in records)})
    print('Sealed',len(records),'paths;',sum(r['bytes']for r in records),'logical bytes')
if __name__=='__main__':main()
