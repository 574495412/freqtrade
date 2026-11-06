"""Capture fixed-universe Binance samples at the L origin and the public S day."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE.parent / 'rd_program_20261105/data_extension'
sys.path.insert(0, str(DATA))
from probe_event_sources import CAP, EXPANDED, fetch, now, sha, write

ROADMAP = DATA.parent / 'plan.json'
FROZEN = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
SHORT = HERE.parent / 'factor_cost_futures_20261106/plan.json'
OUT = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1/universe_depth_v001'
PLAN = HERE / 'registration.json'
RESULT = HERE / 'capture_receipts.json'
DAYS = {'L': '2025-01-01', 'S': '2026-11-01'}
GROUP_SIZE = 4


def verify(plan):
    for path, expected in plan['input_hashes'].items():
        if sha((ROOT/path).read_bytes()) != expected:
            raise ValueError('Registered input changed: '+path)


def register():
    intervals = {'L': FROZEN, 'S': SHORT}
    risk = json.loads(ROADMAP.read_text())
    markets = {key: value['pairs'] for key,value in risk['markets'].items()}
    cases = []
    for window, day in DAYS.items():
        run = json.loads(intervals[window].read_text())['runs'][0]
        if not run['start'][:10] <= day < run['end'][:10]:
            raise ValueError('Sample is outside frozen window')
        for market, pairs in markets.items():
            if len(pairs) != len(set(pairs)):
                raise ValueError('Frozen universe has duplicates')
            exchange = 'binance' if market == 'spot' else 'binance-futures'
            for offset in range(0, len(pairs), GROUP_SIZE):
                group = pairs[offset:offset+GROUP_SIZE]
                symbols = [pair.split(':')[0].replace('/','').lower() for pair in group]
                key = f'{market}-{window}-{offset//GROUP_SIZE:02d}'
                for name, minute, width, channels in (
                        ('depth', 0, 6, ['depthSnapshot','depth']),
                        ('bbo', 4, 2, ['bookTicker'])):
                    filters = [dict(channel=channel, symbols=symbols) for channel in channels]
                    query = urlencode({'from':day, 'offset':minute, 'sliceSize':width,
                                       'filters':json.dumps(filters,separators=(',',':'))})
                    cases.append(dict(id=f'{key}-{name}', group=key, market=market, window=window,
                                      day=day, kind=name, minute=minute, width=width, pairs=group,
                                      symbols=symbols,
                                      url=f'https://api.tardis.dev/v1/data-feeds/{exchange}?{query}'))
    paths = (Path(__file__), ROADMAP, FROZEN, SHORT, DATA/'probe_event_sources.py', DATA/'book_replay.py')
    write(PLAN,dict(registered_at_utc=now(), cases=cases, maximum_requests=len(cases),
                    workers=4, retries=0, timeout_seconds=30, raw_cap_bytes=CAP,
                    decoded_cap_bytes=EXPANDED, cutoff_minute=5, liquidity_freshness_seconds=15,
                    input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in paths},
                    scope='Entire frozen universe at two UTC cutoffs. A sampled symbol is not a full-window admission.',
                    financial_accounts=0))


def capture(case):
    row = dict(case, started_at_utc=now())
    try:
        status, headers, raw, state = fetch(case['url'])
        row.update(http_status=status, headers=headers, response_state=state)
        if raw is not None:
            path = OUT/(case['id']+'.raw')
            with path.open('xb') as handle:
                handle.write(raw)
            row.update(path=str(path.relative_to(ROOT)), bytes=len(raw), sha256=sha(raw))
    except Exception as exc:
        row['error_type'] = type(exc).__name__
    row['finished_at_utc'] = now()
    return row


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    OUT.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(capture, plan['cases']))
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), receipts=rows,
                       historical_depth_admission=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
