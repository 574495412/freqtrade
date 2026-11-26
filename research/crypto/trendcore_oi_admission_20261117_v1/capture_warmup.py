"""Checksum-verified OI warmup archives, with bounded read-only HTTP workers."""
import concurrent.futures
import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

HERE=Path(__file__).absolute().parent
LOCAL=threading.local()


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')


def capture(day):
    name=f'BTCUSDT-metrics-{day}.zip';path=HERE/'raw'/name;checksum=path.with_suffix('.CHECKSUM');receipt=path.with_suffix('.receipt.json')
    url='https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/'+name
    if receipt.exists():
        r=json.loads(receipt.read_text());assert sha(path)==r['sha256']and sha(checksum)==r['checksum_sha256'];return r
    if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
    attempts=[]
    for index in range(2):
        try:
            r=LOCAL.session.get(url,timeout=20);s=LOCAL.session.get(url+'.CHECKSUM',timeout=20)
            attempts.append(dict(attempt=index+1,status=r.status_code,checksum_status=s.status_code))
            r.raise_for_status();s.raise_for_status()
            digest=hashlib.sha256(r.content).hexdigest();expected,filename=s.text.strip().split()
            assert digest==expected and filename==name
            if path.exists():assert sha(path)==digest
            else:path.write_bytes(r.content)
            if checksum.exists():assert checksum.read_bytes()==s.content
            else:checksum.write_bytes(s.content)
            value=dict(day=day,url=url,path=str(path),sha256=digest,checksum_sha256=sha(checksum),retrieved_at=datetime.now(UTC).isoformat(),attempts=attempts)
            save(receipt,value);return value
        except (requests.RequestException,AssertionError)as exc:
            attempts[-1:]=[dict(attempt=index+1,error=str(exc),**(attempts[-1]if attempts else{}))]
            if isinstance(exc,AssertionError)or (isinstance(exc,requests.HTTPError)and exc.response.status_code in [404,418,429]):break
    value=dict(day=day,url=url,failed=True,attempts=attempts);save(path.with_suffix('.failure.json'),value);return value


def main():
    (HERE/'raw').mkdir(exist_ok=True)
    days=[str(d.date())for d in pd.date_range('2024-01-01','2024-12-27',freq='D')]
    expected=json.loads((HERE/'capture_freeze.json').read_text())['days'];assert days==expected
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4)as pool:
        for future in concurrent.futures.as_completed([pool.submit(capture,d)for d in days]):
            rows.append(future.result())
            if len(rows)%64==0:print('CAPTURED',len(rows),'of',len(days),flush=True)
    rows.sort(key=lambda r:r['day']);save(HERE/'warmup_receipts.json',rows)
    failed=[r['day']for r in rows if r.get('failed')]
    save(HERE/'capture_result.json',dict(passed=not failed,expected_days=len(days),captured=len(rows)-len(failed),failed=failed,economic_results_computed=False))
    print('COMPLETE',len(rows)-len(failed),'captured',len(failed),'failed',flush=True)


if __name__=='__main__':main()
