"""Verify completed native workers; distinguish live PID from historical pointer."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import psutil

HERE=Path(__file__).absolute().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root']);completed=[]
    for c in plan['candidates']:
        root=out/(c['name']+f"_{c['cost_bps']}bps");path=root/'process_receipt.json'
        if not path.exists():continue
        r=json.loads(path.read_text());assert r['returncode']==0 and hashlib.sha256((root/'result.json').read_bytes()).hexdigest()==r['result_sha256']
        completed.append(c['name'])
    active=json.loads((out/'active_process.json').read_text());alive=False
    if psutil.pid_exists(active['pid']):
        process=psutil.Process(active['pid']);command=process.cmdline();alive=any('har_vol_source_native_20261117_v1.native_runner'in s for s in command)and process.is_running()
    record=dict(checked_at=datetime.now(UTC).isoformat(),complete=len(completed),expected=8,completed_accounts=completed,active_account=active['account'],active_pid=active['pid'],native_worker_pid_verified_live=alive,all_processes_terminal=len(completed)==8 and not alive,financial_rules_changed=False)
    (HERE/'checkpoint_receipt.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))


if __name__=='__main__':main()
