"""Acquire official and collector public trades at registered rule transitions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent/'historical_l2_hourly_20261106'
sys.path.insert(0,str(PRIOR))
from capture import ENDPOINT, OUT, ROOT, capture, now, sha, verify, write
from download import PLAN as PRIOR_PLAN, download

NOTICES = HERE/'notices.json'
SNAPSHOTS = HERE.parent/'historical_rule_archive_20261106/verification.json'
FROZEN = HERE.parent/'factor_cost_futures_long_20261106/plan.json'
PLAN = HERE/'capture_registration.json'
RESULT = HERE/'capture_receipts.json'
NOTICE_BASE = 'https://www.binance.com/en/support/announcement/detail/'


def register():
    old = json.loads(PRIOR_PLAN.read_text())
    verify(old)
    source = json.loads(SNAPSHOTS.read_text())
    diffs = {}
    for row in source['consecutive_point_differences']:
        for pair,change in row['changes'].items():
            if 'tickSize' in change:
                diffs[pair] = (tuple(map(Decimal,change['tickSize'])),row['before'],row['after'])
    frozen = json.loads(FROZEN.read_text())['runs'][0]
    inputs = [Path(__file__), NOTICES, SNAPSHOTS, FROZEN, PRIOR_PLAN]
    cases, transitions = [], []
    for notice in json.loads(NOTICES.read_text()):
        cutoff = datetime.fromisoformat(notice['deadline'].replace('Z','+00:00'))
        day, hour = cutoff.date().isoformat(),cutoff.hour
        if not frozen['start'][:10]<=day<frozen['end'][:10] or hour==0:
            raise ValueError('Transition is outside the frozen L or bounded hour design')
        market = notice['market']
        for pair,before,after in zip(notice['pairs'],notice['old'],notice['new'],strict=True):
            values,left,right = diffs[pair]
            if values!=(Decimal(before),Decimal(after)):
                raise ValueError('Notice old/new values disagree with snapshot observations')
            if not left.split('-')[1] < cutoff.strftime('%Y%m%d%H%M%S') < right.split('-')[1]:
                raise ValueError('Notice time lies outside the bracketing snapshots')
            symbol = pair.split(':')[0].replace('/','')
            key = f'{market}-{day}-{symbol}'
            transition = dict(key=key,pair=pair,symbol=symbol,market=market,day=day,
                              deadline=notice['deadline'],suspension_end=notice.get('suspension_end'),
                              old_tick=before,new_tick=after,notice_published_date=notice['published_date'],
                              url=NOTICE_BASE+notice['id'],brackets=[left,right])
            transitions.append(transition)
            folder = 'spot' if market=='spot' else 'futures/um'
            archive = f'https://data.binance.vision/data/{folder}/daily/trades/{symbol}/{symbol}-trades-{day}.zip'
            common = dict(group=key,symbol=symbol,market=market,day=day)
            cases.extend((
                dict(common,id=f'tick-{key}-zip',source='official_zip',url=archive),
                dict(common,id=f'tick-{key}-checksum',source='official_checksum',url=archive+'.CHECKSUM'),
            ))
            for h in (hour-1,hour):
                file = f'binance_{market}/{day}/{h:02d}/{symbol}_trades.parquet'
                cases.append(dict(common,id=f'tick-{key}-hour-{h:02d}',source='collector',
                                  hour=h,file=file,url=ENDPOINT+'?'+urlencode({'file':file})))
    if len(transitions)!=12:
        raise ValueError('Unexpected transition set')
    write(PLAN,dict(registered_at_utc=now(),transitions=transitions,cases=cases,workers=3,
                    maximum_requests=len(cases)+2*len(transitions),retries=0,
                    socket_timeout_seconds=old['socket_timeout_seconds'],
                    max_bytes_per_response=old['max_bytes_per_response'],storage_host=old['storage_host'],
                    input_hashes={**old['input_hashes'],**{
                        str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in inputs}},
                    financial_accounts=0))


def acquire(case, plan):
    if case['source']=='collector':
        return download(case,plan)
    if case['source']=='official_checksum':
        plan = dict(plan,max_bytes_per_response=4096)
    return capture(case,plan)


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT/(case['id']+'.raw')).exists() for case in plan['cases']):
        raise FileExistsError('Transition acquisition already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as workers:
        rows = list(workers.map(lambda case:acquire(case,plan),plan['cases']))
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),receipts=rows,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
