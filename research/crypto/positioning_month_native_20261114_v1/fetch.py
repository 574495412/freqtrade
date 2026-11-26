"""Record each archive outcome; recover only independently checksum-verified bytes."""
import hashlib,json,shutil
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import UTC,datetime
from pathlib import Path
import pandas as pd
import requests
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OUT=ROOT/'user_data/crypto_quant/positioning_month_native_20261114_v1'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
old=json.loads((ROOT/'user_data/crypto_quant/oi_cascade_source_native_20261113_v1/raw_receipts.json').read_text())
reused={(r.get('symbol','BTCUSDT'),r['day']):r for r in old}
receipt=OUT/'raw_receipts.json'
previous={(r['symbol'],r['day']):r for r in json.loads(receipt.read_text())}if receipt.exists()else{}
def get(task):
 symbol,day=task;key=(symbol,day);name=f'{symbol}-metrics-{day}.zip';url=f'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{name}'
 if key in previous:
  r=previous[key];assert sha(r['path'])==r['sha256'];return r
 if key in reused:
  r=reused[key];assert sha(r['path'])==r['sha256'];return dict(r,symbol=symbol,reused_prior_verified_archive=True)
 p=OUT/'raw'/name;check=p.with_suffix('.zip.CHECKSUM');attempts=[]
 # v1 completed some request pairs before its result collector stopped. These
 # files are not assumed good: compare their actual bytes to saved official SHA.
 if p.exists()and check.exists():
  digest=sha(p)
  if digest==check.read_text().split()[0]:
   return {'symbol':symbol,'day':day,'url':url,'path':str(p),'sha256':digest,'checksum_path':str(check),
    'checksum_sha256':sha(check),'reused_prior_verified_archive':False,'recovered_verified_orphan':True,
    'verified_at':datetime.now(UTC).isoformat()}
 for attempt in range(2):
  try:
   assert shutil.disk_usage(ROOT).free>=2*1024**3,'Disk below2GiB before new public archive'
   x=requests.get(url,timeout=25);x.raise_for_status();y=requests.get(url+'.CHECKSUM',timeout=25);y.raise_for_status()
   digest=hashlib.sha256(x.content).hexdigest();assert digest==y.text.split()[0],name
   # A bad/partial v1 file is preserved at its existing path; replacement goes
   # under a distinct attempt folder rather than overwriting failed evidence.
   if p.exists():
    p=OUT/'raw_retry'/name;p.parent.mkdir(exist_ok=True);check=p.with_suffix('.zip.CHECKSUM')
   tmp=p.with_suffix('.zip.writing');tmp.write_bytes(x.content);tmp.replace(p)
   tmp=check.with_suffix('.CHECKSUM.writing');tmp.write_bytes(y.content);tmp.replace(check)
   return {'symbol':symbol,'day':day,'url':url,'path':str(p),'sha256':digest,'checksum_path':str(check),
    'checksum_sha256':sha(check),'retrieved_at':datetime.now(UTC).isoformat(),'reused_prior_verified_archive':False,'request_failures':attempts}
  except Exception as exc:
   attempts.append({'attempt':attempt+1,'error':repr(exc),'at':datetime.now(UTC).isoformat()})
 return {'symbol':symbol,'day':day,'url':url,'error':'UNVERIFIED_ARCHIVE','request_failures':attempts}
def save(path,data):
 temp=path.with_suffix(path.suffix+'.writing');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)
def main():
 tasks=[(s,d.strftime('%Y-%m-%d'))for s in ['BTCUSDT','ETHUSDT','SOLUSDT']for d in pd.date_range('2024-01-01','2026-08-31')]
 records=[];failures=[]
 with ThreadPoolExecutor(max_workers=6)as pool:
  futures={pool.submit(get,t):t for t in tasks}
  for i,f in enumerate(as_completed(futures),1):
   try:r=f.result()
   except Exception as exc:
    symbol,day=futures[f];r={'symbol':symbol,'day':day,'error':repr(exc)}
   (failures if 'error'in r else records).append(r)
   if i%30==0 or i==len(tasks):
    save(receipt,sorted(records,key=lambda r:(r['symbol'],r['day'])))
    save(OUT/'download_failures.json',failures)
    print('Observed',i,'/',len(tasks),'verified',len(records),'failed',len(failures),flush=True)
 assert len(records)==len(tasks)and not failures,'Preserve missing/failed archives; do not start finance'
 print('Complete official3symbol archive matrix',len(records),flush=True)
if __name__=='__main__':main()
