"""Isolated dependency bootstrap; delegate the actual account to native runner."""
import argparse
import fcntl
import hashlib
import json
import os
import traceback
from pathlib import Path

from research.crypto.freqai_rf_native_20261114_v1.bootstrap import boot

HERE=Path(__file__).resolve().parent


def check_dependencies():
    manifest=json.loads((HERE/'dependency_manifest.json').read_text())
    for relative,record in manifest['files'].items():
        assert hashlib.sha256((HERE/'dependencies'/relative).read_bytes()).hexdigest()==record,relative
    assert os.environ.get('DYLD_LIBRARY_PATH')==str(HERE/'dependencies/sklearn/.dylibs')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--candidate',required=True);parser.add_argument('--cost-bps',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:
        check_dependencies();boot()
        from research.crypto.tsmom_risk_native_20261114_v1.native_runner import run,save
        with (args.plan.parent/'financial_worker.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run(args.plan.resolve(),args.candidate,args.cost_bps,args.output.resolve())
            check_dependencies()
    except BaseException as exc:
        if args.output.exists():
            (args.output/'failure.json').write_text(json.dumps(dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()),indent=2))
        raise
