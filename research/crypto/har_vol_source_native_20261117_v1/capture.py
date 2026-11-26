"""Bounded checksum-pinned primary 1m capture; no forecasts or accounts."""
import concurrent.futures
import hashlib
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE=Path(__file__).absolute().parent
LOCAL=threading.local()


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')


def capture(job):
    symbol,month=job['symbol'],job['month'];name=f'{symbol}-1m-{month}.zip';path=HERE/'raw'/symbol/name;path.parent.mkdir(exist_ok=True)
    receipt=path.with_suffix('.receipt.json');checksum=path.with_suffix('.CHECKSUM');url=f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/1m/{name}'
    if receipt.exists():
        r=json.loads(receipt.read_text());assert sha(path)==r['sha256']and sha(checksum)==r['checksum_sha256'];return r
    if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
    attempts=[]
    for attempt in range(2):
        try:
            if shutil.disk_usage(HERE).free<1024**3:raise RuntimeError('Retain1GiBfreebeforemoredata')
            r=LOCAL.session.get(url,timeout=30);s=LOCAL.session.get(url+'.CHECKSUM',timeout=20);attempts.append(dict(attempt=attempt+1,status=r.status_code,checksum_status=s.status_code));r.raise_for_status();s.raise_for_status()
            expected,filename=s.text.strip().split();digest=hashlib.sha256(r.content).hexdigest();assert filename==name and digest==expected
            if path.exists():assert sha(path)==digest
            else:path.write_bytes(r.content)
            if checksum.exists():assert checksum.read_bytes()==s.content
            else:checksum.write_bytes(s.content)
            value=dict(**job,url=url,path=str(path),sha256=digest,checksum_sha256=sha(checksum),bytes=len(r.content),retrieved_at=datetime.now(UTC).isoformat(),attempts=attempts);save(receipt,value);return value
        except (requests.RequestException,AssertionError,RuntimeError)as exc:
            attempts.append(dict(error=str(exc),attempt=attempt+1))
            if isinstance(exc,(AssertionError,RuntimeError))or(isinstance(exc,requests.HTTPError)and exc.response.status_code in [404,418,429]):break
    value=dict(**job,failed=True,attempts=attempts,url=url);save(path.with_suffix('.failure.json'),value);return value


def main():
    (HERE/'raw').mkdir(exist_ok=True);jobs=json.loads((HERE/'capture_jobs.json').read_text());rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
        for future in concurrent.futures.as_completed([pool.submit(capture,j)for j in jobs]):
            rows.append(future.result())
            if len(rows)%12==0:print('CAPTURE',len(rows),'of',len(jobs),flush=True)
    rows.sort(key=lambda r:(r['symbol'],r['month']));save(HERE/'raw_receipts.json',rows)
    failures=[dict(symbol=r['symbol'],month=r['month'])for r in rows if r.get('failed')];save(HERE/'capture_result.json',dict(passed=not failures,captured=len(rows)-len(failures),failed=failures,expected=len(jobs)))
    print('COMPLETE',len(rows)-len(failures),'captured',len(failures),'failed',flush=True)


if __name__=='__main__':main()
