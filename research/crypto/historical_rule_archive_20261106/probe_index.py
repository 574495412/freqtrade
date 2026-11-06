"""Bounded public index lookup for Binance rule snapshots in the frozen windows."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HELPER = HERE.parent/'rd_program_20261105'/'data_extension'/'probe_event_sources.py'
sys.path.insert(0,str(HELPER.parent))
from probe_event_sources import decode, fetch, now, sha, write, CAP, EXPANDED

PLAN = HERE/'registration.json'
OUT = ROOT/'user_data/crypto_quant/rd_program_20261105_v1/exchangeinfo_archive_probe_v001'
ORIGINS = {
    'spot': 'https://api.binance.com/api/v3/exchangeInfo',
    'futures': 'https://fapi.binance.com/fapi/v1/exchangeInfo',
}
WINDOWS = {
    'L': ('20250101','20260901'),
    'S': ('20261023','20261105'),
}
OBSERVATIONS = ('20250101','20250408','20260414','20261023')


def cases():
    rows = []
    for market,origin in ORIGINS.items():
        for target in OBSERVATIONS:
            rows.append(dict(id=f'{market}-availability-{target}',kind='availability',market=market,
                origin=origin,target=target,url='https://archive.org/wayback/available?'+urlencode(dict(url=origin,timestamp=target))))
        for window,(start,end) in WINDOWS.items():
            query = urlencode([('url',origin),('matchType','exact'),('from',start),('to',end),
                ('output','json'),('fl','timestamp,original,statuscode,mimetype,digest'),('limit','50')])
            rows.append(dict(id=f'{market}-cdx-{window}',kind='cdx',market=market,origin=origin,window=window,
                url='https://web.archive.org/cdx/search/cdx?'+query))
    return rows


def match_window(stamp):
    date = datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    return [window for window,(start,end) in WINDOWS.items()
            if datetime.strptime(start,'%Y%m%d').replace(tzinfo=timezone.utc)<=date
            <datetime.strptime(end,'%Y%m%d').replace(tzinfo=timezone.utc)]


def probe(case):
    result = dict(case,started_at_utc=now())
    try:
        status,headers,raw,state = fetch(case['url'])
        result.update(http_status=status,headers=headers,response_state=state)
        if raw is not None:
            path = OUT/(case['id']+'.raw')
            with path.open('xb') as handle:
                handle.write(raw)
            result.update(path=str(path.relative_to(ROOT)),bytes=len(raw),sha256=sha(raw))
        if status==200 and state=='body_complete':
            payload = json.loads(decode(raw,headers))
            if case['kind']=='availability':
                closest = payload['archived_snapshots'].get('closest')
                result['closest_index_record'] = closest
                if closest is not None:
                    result['closest_inside_windows'] = match_window(closest['timestamp'])
            else:
                if payload and payload[0]!=['timestamp','original','statuscode','mimetype','digest']:
                    raise ValueError('Unexpected CDX header')
                result['index_records'] = [dict(timestamp=t,original=o,status=s,mimetype=m,digest=d,inside_windows=match_window(t))
                    for t,o,s,m,d in payload[1:]]
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    result['finished_at_utc']=now()
    return result


def verify(plan):
    for path,expected in plan['input_hashes'].items():
        if sha((ROOT/path).read_bytes())!=expected:
            raise ValueError('Registered input drift: '+path)


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        short = HERE.parent/'factor_cost_futures_20261106'/'plan.json'
        long = HERE.parent/'factor_cost_futures_long_20261106'/'plan.json'
        sources = [Path(__file__),HELPER,short,long]
        for window,path in [('L',long),('S',short)]:
            row=json.loads(path.read_text())['runs'][0]
            if tuple(row[k][:10].replace('-','') for k in ('start','end'))!=WINDOWS[window]:
                raise ValueError('Archive windows do not match the frozen accounts')
        rows=cases()
        write(PLAN,dict(registered_at_utc=now(),cases=rows,maximum_requests=len(rows),workers=3,timeout_seconds=30,
            retries=0,raw_cap_bytes=CAP,expanded_cap_bytes=EXPANDED,
            input_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in sources},
            scope='index availability only; snapshot bodies and rule validity require independent follow-up',financial_accounts=0))
    elif sys.argv[1:]==['run']:
        plan=json.loads(PLAN.read_text());verify(plan)
        OUT.mkdir(parents=True,exist_ok=False)
        with ThreadPoolExecutor(max_workers=plan['workers']) as workers:
            rows=list(workers.map(probe,plan['cases']))
        verify(plan)
        write(HERE/'index_result.json',dict(registration_sha256=sha(PLAN.read_bytes()),rows=rows,
            historical_rule_admission=False,financial_accounts=0))
    else:
        raise SystemExit('Use register or run')
