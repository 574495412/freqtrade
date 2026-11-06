"""Bounded public-only schema/access probes; no credentials or financial runs."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
import csv,gzip,hashlib,io,json,zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=ROOT/'user_data/crypto_quant/rd_program_20261105_v1/event_source_probe_v001'
PLAN=HERE/'event_source_probe_registration.json'
CAP=8*1024*1024
EXPANDED=32*1024*1024


def sha(data):return hashlib.sha256(data).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def write(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


def requests():
    result=[]
    for exchange in ['binance','binance-futures']:
        result.append({'id':exchange+'_metadata','kind':'metadata','url':'https://api.tardis.dev/v1/exchanges/'+exchange})
        for date in ['2025-01-01','2025-01-02']:
            channels=['depth','depthSnapshot','trade']+(['markPrice'] if exchange=='binance-futures' else [])
            params={'from':date,'offset':0,'filters':json.dumps([{'channel':c,'symbols':['btcusdt']} for c in channels],separators=(',',':'))}
            result.append({'id':exchange+'_'+date,'kind':'minute','url':'https://api.tardis.dev/v1/data-feeds/'+exchange+'?'+urlencode(params)})
    for kind in ['bookDepth','bookTicker']:
        result.append({'id':'binance_public_'+kind,'kind':'archive','url':f'https://data.binance.vision/data/futures/um/daily/{kind}/BTCUSDT/BTCUSDT-{kind}-2025-01-01.zip'})
    return result


def fetch(url):
    req=Request(url,headers={'Accept-Encoding':'gzip','User-Agent':'bounded-research-schema-probe'})
    try:response=urlopen(req,timeout=30)
    except HTTPError as exc:response=exc
    with response:
        headers={k:v for k,v in response.headers.items() if k.lower() in ['content-type','content-encoding','content-length','etag','last-modified']}
        length=int(response.headers.get('Content-Length','0'))
        if length>CAP:return response.status,headers,None,'declared_body_exceeds_cap'
        body=response.read(CAP+1)
        if len(body)>CAP:return response.status,headers,body,'truncated_at_cap'
        return response.status,headers,body,'body_complete'


def decode(body,headers):
    encoding=next((v for k,v in headers.items() if k.lower()=='content-encoding'),'')
    if encoding=='gzip':
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:data=f.read(EXPANDED+1)
    elif not encoding:data=body
    else:raise ValueError('Unsupported encoding: '+encoding)
    if len(data)>EXPANDED:raise ValueError('Expanded response exceeds cap')
    return data


def probe(item):
    r=dict(item,started_at_utc=now())
    try:
        status,headers,body,state=fetch(item['url'])
        r.update(http_status=status,headers=headers,state=state)
        if body is not None:
            path=OUT/(item['id']+'.raw')
            with path.open('xb') as f:f.write(body)
            r['raw']={'path':str(path.relative_to(ROOT)),'bytes':len(body),'sha256':sha(body)}
        if state!='body_complete':return r
        data=decode(body,headers)
        if status!=200:
            r['error_body_excerpt']=data.decode(errors='replace')[:1000];return r
        if item['kind']=='metadata':
            m=json.loads(data)
            r['metadata_keys']=list(m)
            r['available_symbols']=len(m.get('availableSymbols',[]))
            r['btc_metadata']=[s for s in m.get('availableSymbols',[]) if s.get('id','').lower()=='btcusdt']
        elif item['kind']=='minute':
            streams={};counts={};times=[];disconnects=0;first={}
            for line in data.decode().splitlines():
                if not line.strip():disconnects+=1;continue
                local,message=line.split(' ',1);payload=json.loads(message)
                at=datetime.fromisoformat(local.replace('Z','+00:00'))
                times.append(at)
                stream=payload.get('stream','unknown');streams[stream]=streams.get(stream,0)+1
                value=payload.get('data',{})
                counts[stream]=sorted(value) if isinstance(value,dict) else []
                first.setdefault(stream,{'local_timestamp':local,'payload_top_keys':sorted(payload)})
            r['minute_schema']={'messages':len(times),'disconnect_markers':disconnects,'streams':streams,
                'data_fields':counts,'first_message_metadata':first,'local_timestamp_monotonic':all(a<=b for a,b in zip(times,times[1:])),
                'first_local_timestamp':times[0].isoformat() if times else None,'last_local_timestamp':times[-1].isoformat() if times else None}
        else:
            cs,ch,cb,st=fetch(item['url']+'.CHECKSUM')
            if cb is not None:
                with (OUT/(item['id']+'.CHECKSUM')).open('xb') as f:f.write(cb)
            if cs!=200 or st!='body_complete':raise ValueError('Official checksum unavailable')
            expected=decode(cb,ch).decode().split()[0]
            if sha(data)!=expected:raise ValueError('Official checksum mismatch')
            r['official_checksum_verified']=True
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                files=z.infolist()
                if len(files)!=1 or files[0].file_size>EXPANDED:raise ValueError('Archive expansion cap or unexpected members')
                with z.open(files[0]) as stream:
                    rows=csv.reader(io.TextIOWrapper(stream));header=next(rows);n=0;first=[];percentages=set()
                    for row in rows:
                        n+=1
                        if len(first)<2:first.append(row)
                        if 'percentage' in header:percentages.add(row[header.index('percentage')])
                    r['archive_schema']={'columns':header,'rows':n,'first_rows':first,'percentage_values':sorted(percentages)}
    except Exception as exc:r['error']=type(exc).__name__+': '+str(exc)
    finally:r['finished_at_utc']=now()
    return r


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['register']:
        write(PLAN,{'registered_at_utc':now(),'source_sha256':sha(Path(__file__).read_bytes()),'requests':requests(),
                    'requests_max_including_conditional_checksums':10,'workers':2,'timeout_seconds':30,
                    'raw_cap_bytes':CAP,'expanded_cap_bytes':EXPANDED,'retries':0,
                    'output_directory':str(OUT.relative_to(ROOT)),'financial_runs':0,'orders_enabled':False,
                    'access':'public unauthenticated requests only; no credential lookup, subscription or purchase'})
    else:
        plan=json.loads(PLAN.read_text())
        if plan['source_sha256']!=sha(Path(__file__).read_bytes()) or plan['requests']!=requests():raise ValueError('Registration drift')
        OUT.mkdir(exist_ok=False)
        write(OUT/'start.json',{'started_at_utc':now(),'registration_sha256':sha(PLAN.read_bytes())})
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(probe,plan['requests']))
        write(OUT/'result.json',{'completed_at_utc':now(),'probes':results,'financial_runs':0,'economic_qualified':False})
        for r in results:print(json.dumps({k:r[k] for k in ['id','http_status','state','error','minute_schema','archive_schema'] if k in r}),flush=True)
