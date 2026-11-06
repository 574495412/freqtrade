"""Run the pinned synthetic suite once; never a financial backtest entry point."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()


def main():
    registration=HERE/'native_seam_registration.json'
    frozen=json.loads(registration.read_text())
    for relative,expected in frozen['source_hashes'].items():
        path=(ROOT/relative).resolve()
        if not path.is_relative_to(ROOT) or sha(path)!=expected:
            raise ValueError('registered source changed: '+relative)
    if frozen['evidence_class']!='synthetic' or frozen['financial_runs']!=0:
        raise ValueError('this runner supports only synthetic engineering checks')
    # Fixed, non-deployment destination. No caller-supplied working directory.
    output=ROOT/'user_data/crypto_quant/rd_program_20261105_v1/native_seam_v001'
    output.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,'-B','-m','unittest','discover','-s',str(HERE),'-p','test_*.py','-v']
    start={'started_at_utc':now(),'registration_sha256':sha(registration),'command':command,
           'evidence_class':'synthetic','orders_enabled':False}
    (output/'start.json').write_text(json.dumps(start,indent=2)+'\n')
    returncode=None;timeout=False
    with (output/'checks.log').open('xb') as stream:
        try:
            result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=120)
            returncode=result.returncode
        except subprocess.TimeoutExpired:timeout=True
    log=(output/'checks.log').read_text()
    count=re.search(r'Ran (\d+) tests? in ',log)
    passed=returncode==0 and count is not None and int(count[1])==frozen['expected_tests']
    receipt=dict(start,finished_at_utc=now(),returncode=returncode,timeout=timeout,
        tests_run=int(count[1]) if count else None,passed=passed,log_sha256=sha(output/'checks.log'),
        financial_runs=0,production_eligible=False,economic_qualified=False,
        source_order_certified=False,real_exchange_submission=False,
        actual_native_methods=['execute_entry','update_trade_state','handle_cancel_enter',
                               'Order.parse_from_ccxt_object','Trade.update_trade'],
        mocked_boundaries=frozen['mocked_boundaries'])
    (output/'result.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'output':str(output),'tests_run':receipt['tests_run'],'passed':passed}))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
