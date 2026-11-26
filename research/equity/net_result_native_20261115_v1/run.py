"""Serial offline synthetic native starts; never a historical account controller."""
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def main():
    frozen=json.loads((HERE/'implementation_freeze.json').read_text())
    receipts=[]
    for market in ['XNYS','XHKG']:
        for case in ['base','extrema','held']:
            for r in frozen:
                assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
            assert shutil.disk_usage(ROOT).free>=2*1024**3
            name=market+'_'+case+'_main'
            command=[sys.executable,'-B',str(HERE/'native_probe.py'),'--market',market,'--attempt',name]
            if case=='extrema':command+=['--future-extrema']
            if case=='held':command+=['--terminal-held']
            with (HERE/(name+'.log')).open('wb')as log:
                started=datetime.now(UTC).isoformat()
                child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
                receipt=dict(case=market+'_'+case,attempt=name,pid=child.pid,command=command,started_at=started)
                try:returncode=child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait();raise
                receipt.update(returncode=returncode,finished_at=datetime.now(UTC).isoformat())
                receipts.append(receipt)
                (HERE/'execution_receipt.json').write_text(json.dumps(receipts,indent=2)+'\n')
                assert returncode==0,name
            print(name,'complete',flush=True)


if __name__=='__main__':main()
