"""Capture registered public funding and order-book samples without authentication."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HELPERS = HERE.parent / 'rd_program_20261105' / 'data_extension'
sys.path.insert(0, str(HELPERS))
from probe_event_sources import CAP, EXPANDED, fetch, now, sha, write

PLAN = HERE / 'registration.json'
OUT = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1/funding_clock_probe_v001'
DAY = '2026-11-01'
SYMBOLS = ('BTCUSDT', 'SOLUSDT')
BASE = ROOT / 'user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001/funding_api'
SHORT_PLAN = HERE.parent / 'factor_cost_futures_20261106' / 'plan.json'


def sample(day, offset, channels):
    filters = [dict(channel=channel, symbols=[s.lower() for s in SYMBOLS]) for channel in channels]
    query = urlencode(dict(**{'from':day}, offset=offset, filters=json.dumps(filters,separators=(',',':'))))
    return dict(id=f'{day}_{offset:04d}_{channels[0]}', day=day, offset_minutes=offset,
                channels=channels, url='https://api.tardis.dev/v1/data-feeds/binance-futures?'+query)


def register():
    short = json.loads(SHORT_PLAN.read_text())['runs'][0]
    if not short['start'][:10] <= DAY < short['end'][:10]:
        raise ValueError('Sample day is outside the frozen short window')
    base = ROOT / short['datadir']
    data_receipt = ROOT / short['data_receipt']
    hashes = json.loads(data_receipt.read_text())
    feathers = [f'futures/{s[:-4]}_USDT_USDT-1h-funding_rate.feather' for s in SYMBOLS]
    for path in feathers:
        if sha((base/path).read_bytes()) != hashes[path]:
            raise ValueError('Frozen funding input differs from original receipt')
    paths = [Path(__file__), HELPERS/'probe_event_sources.py', HELPERS/'book_replay.py', SHORT_PLAN, data_receipt,
             *(BASE/(s+'.json') for s in SYMBOLS), *(base/p for p in feathers)]
    cases = [sample(DAY, minute, ['fundingRate']) for minute in (5,65,485,545,965,1025)]
    cases += [sample(DAY,5,['depth','depthSnapshot','bookTicker','trade','markPrice']),
              sample('2026-11-02',5,['fundingRate'])]
    write(PLAN,dict(registered_at_utc=now(), target_symbols=SYMBOLS, target_day=DAY,
        input_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        source_sample_cases=cases, maximum_requests=len(cases), retries=0, workers=2,
        timeout_seconds=30, raw_cap=CAP, expanded_cap=EXPANDED,
        scope='source arrival and aggregate funding-rate audit; no private funding or economic qualification',
        financial_accounts=0))


def verify(plan):
    for path, expected in plan['input_hashes'].items():
        if sha((ROOT/path).read_bytes()) != expected:
            raise ValueError('Registered source drift: '+path)


def capture(item):
    receipt = dict(item, started_at_utc=now())
    try:
        status, headers, raw, state = fetch(item['url'])
        receipt.update(http_status=status, headers=headers, response_state=state)
        if raw is not None:
            path = OUT/(item['id']+'.raw')
            with path.open('xb') as handle:
                handle.write(raw)
            receipt.update(path=str(path.relative_to(ROOT)),bytes=len(raw),sha256=sha(raw))
    except Exception as exc:
        receipt['error'] = f'{type(exc).__name__}: {exc}'
    receipt['finished_at_utc'] = now()
    return receipt


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    OUT.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=plan['workers']) as workers:
        receipts = list(workers.map(capture,plan['source_sample_cases']))
    verify(plan)
    write(HERE/'capture_receipts.json',dict(registration_sha256=sha(PLAN.read_bytes()),receipts=receipts))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
