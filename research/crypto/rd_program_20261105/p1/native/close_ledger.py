"""Append completed P1 native attempts to the ledger; never execute accounts."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import score_accounts as audit


def main():
    ledger = audit.HERE.parent / 'execution_ledger.jsonl'
    previous = [json.loads(line) for line in ledger.read_text().splitlines()]
    identities = {(r['id'],r.get('attempt','initial'),r.get('kind','control')) for r in previous}
    rows = []
    failed_id = 'P1_B2_AVERAGE_NATIVE_spot_S_base'
    failed_registration = audit.DATA/'registrations'/failed_id
    failure = audit.DATA/'accounts'/failed_id/'failure.json'
    failed_start = audit.read(failed_registration/'start.json')
    rows.append({'id':failed_id,'kind':'native','attempt':'initial','state':'configuration_rejected_before_native_backtest',
        'manifest_sha256':audit.sha(failed_registration/'run_manifest.json'),
        'result_path':str(failure.relative_to(audit.ROOT)),'result_sha256':audit.sha(failure),
        'start_utc':failed_start['start_utc'],'end_utc':None,'financial_backtests':0,
        'failure_code_commit':'ea850dce6','technical_retry_used':1})
    registry = audit.read(audit.PROGRAM/'experiment_registry.json')
    cases = [(run['id'],'','native') for run in registry['runs'] if run['baseline_id'] in ('B2_AVERAGE_NATIVE','B3_NFI_X8_NATIVE')]
    cases += [(r['run_id'],'__determinism1','determinism') for r in audit.read(audit.HERE/'determinism_registration.json')['runs']]
    for run_id,suffix,kind in cases:
        manifest, output = audit.registration(run_id,suffix)
        audit.verify_artifacts(output)
        result = audit.read(output/'p1_result.json')
        registration_name = output.name
        rows.append({'id':run_id,'kind':kind,'attempt':'determinism1' if suffix else ('retry1' if registration_name.endswith('__retry1') else 'initial'),
            'state':'native_engineering_reference_completed',
            'manifest_sha256':audit.sha(audit.DATA/'registrations'/registration_name/'run_manifest.json'),
            'result_path':str((output/'p1_result.json').relative_to(audit.ROOT)),
            'result_sha256':audit.sha(output/'p1_result.json'),
            'start_utc':result['start_utc'],'end_utc':result['finished_at_utc'],
            'financial_backtests':1,'new_unique_account':kind=='native','orders_enabled':False,
            'economic_qualified':False})
    additions = [r for r in rows if (r['id'],r['attempt'],r['kind']) not in identities]
    for row in additions:
        row['recorded_at_utc'] = datetime.now(timezone.utc).isoformat()
    with ledger.open('a') as stream:
        for row in additions: stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        stream.flush();os.fsync(stream.fileno())
    print(json.dumps({'appended':len(additions),'total_entries':len(previous)+len(additions),
                      'native_unique_accounts':16,'determinism_replays':2,'configuration_failures':1}))


if __name__=='__main__':main()
