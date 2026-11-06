"""Register full short-window official public funding and hourly collector polls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE.parent/'rd_program_20261105/data_extension'
sys.path.insert(0,str(DATA))
from probe_event_sources import fetch, now, sha, write

ROADMAP = DATA.parent/'plan.json'
LONG = HERE.parent/'factor_cost_futures_long_20261106/plan.json'
SHORT = HERE.parent/'factor_cost_futures_20261106/plan.json'
OUT = ROOT/'user_data/crypto_quant/rd_program_20261105_v1/funding_universe_v001'
PLAN = HERE/'registration.json'
RESULT = HERE/'capture_receipts.json'
DAY = '2026-11-01'
OFFICIAL = 'https://fapi.binance.com/fapi/v1/fundingRate'


def verify(plan):
    for path, expected in plan['input_hashes'].items():
        if sha((ROOT/path).read_bytes()) != expected:
            raise ValueError('Registered input changed: '+path)


def bounds(path):
    run = json.loads(path.read_text())['runs'][0]
    return tuple(int(datetime.fromisoformat(run[k]).timestamp()*1000) for k in ('start','end'))


def register():
    pairs = json.loads(ROADMAP.read_text())['markets']['futures']['pairs']
    symbols = [pair.split(':')[0].replace('/','') for pair in pairs]
    if len(symbols)!=40 or len(set(symbols))!=40:
        raise ValueError('Frozen futures universe changed')
    start,end = bounds(SHORT)
    if not start<=int(datetime.fromisoformat(DAY+'T00:00:00+00:00').timestamp()*1000)<end:
        raise ValueError('Poll day outside frozen S')
    cases = []
    for symbol in symbols:
        query = urlencode(dict(symbol=symbol,startTime=start,endTime=end-1,limit=1000))
        cases.append(dict(id='official-'+symbol,kind='official',symbol=symbol,url=OFFICIAL+'?'+query))
    for minute in range(5,24*60,60):
        filters = json.dumps([dict(channel='fundingRate',symbols=[s.lower() for s in symbols])],
                             separators=(',',':'))
        query = urlencode({'from':DAY,'offset':minute,'filters':filters})
        cases.append(dict(id=f'collector-{minute:04d}',kind='collector',minute=minute,day=DAY,
                          url='https://api.tardis.dev/v1/data-feeds/binance-futures?'+query))
    paths = (Path(__file__),ROADMAP,LONG,SHORT,DATA/'probe_event_sources.py')
    write(PLAN,dict(registered_at_utc=now(),pairs=pairs,symbols=symbols,cases=cases,
                    maximum_requests=len(cases),official_requests=40,workers=4,retries=0,
                    input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in paths},
                    financial_accounts=0))


def capture(case):
    result = dict(case,started_at_utc=now())
    try:
        status, headers, raw, state = fetch(case['url'])
        result.update(http_status=status,headers=headers,response_state=state)
        if raw is not None:
            path = OUT/(case['id']+'.raw')
            with path.open('xb') as handle:
                handle.write(raw)
            result.update(path=str(path.relative_to(ROOT)),bytes=len(raw),sha256=sha(raw))
    except Exception as exc:
        result['error_type']=type(exc).__name__
    result['finished_at_utc']=now()
    return result


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    OUT.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(capture,plan['cases']))
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),receipts=rows,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
