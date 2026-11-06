"""Register adjacent BTCUSDT L2 partitions using the tested public download path."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / 'historical_l2_hourly_20261106'
sys.path.insert(0, str(PRIOR))
from audit import receipt_body
from capture import ENDPOINT, OUT, ROOT, now, sha, verify, write
from download import PLAN as PRIOR_PLAN, RESULT as PRIOR_RESULT, download

PLAN = HERE / 'capture_registration.json'
RESULT = HERE / 'capture_receipts.json'
OFFSETS = (-3, -2, -1, 1, 2, 3)


def register():
    old = json.loads(PRIOR_PLAN.read_text())
    receipts = json.loads(PRIOR_RESULT.read_text())
    verify(old)
    if receipts['registration_sha256'] != sha(PRIOR_PLAN.read_bytes()):
        raise ValueError('Prior registration hash mismatch')
    target = next(row for row in receipts['receipts']
                  if row['market'] == 'futures' and row['window'] == 'L')
    source = receipt_body(target)
    original_hour = datetime.fromisoformat(target['day']).replace(
        hour=target['hour'], tzinfo=timezone.utc)
    long_plan = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
    interval = json.loads(long_plan.read_text())['runs'][0]
    start, end = (datetime.fromisoformat(interval[key].replace('Z', '+00:00'))
                  for key in ('start', 'end'))
    cases = []
    for offset in OFFSETS:
        at = original_hour + timedelta(hours=offset)
        if not start <= at < end:
            raise ValueError('Adjacent sample is outside frozen L')
        day = at.date().isoformat()
        file = f"binance_futures/{day}/{at.hour:02d}/{target['symbol']}_orderbook.parquet"
        cases.append(dict(id=f"bridge-{day}-{at.hour:02d}-{target['symbol']}", market='futures',
                          window='L', day=day, hour=at.hour, symbol=target['symbol'], file=file,
                          url=ENDPOINT + '?' + urlencode({'file': file})))
    inputs = (Path(__file__), PRIOR / 'audit.py', PRIOR / 'download.py', PRIOR_PLAN,
              PRIOR_RESULT, source, long_plan)
    hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs}
    write(PLAN, dict(registered_at_utc=now(), target=target, cases=cases,
                     workers=old['workers'], max_requests=2*len(cases),
                     storage_host=old['storage_host'], redirects=old['redirects'], retries=0,
                     socket_timeout_seconds=old['socket_timeout_seconds'],
                     max_bytes_per_response=old['max_bytes_per_response'],
                     input_hashes={**old['input_hashes'], **hashes},
                     scope='Three preceding and three following hours for the unanchored L file',
                     financial_accounts=0))


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT / (case['id'] + '.raw')).exists() for case in plan['cases']):
        raise FileExistsError('Adjacent downloads already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda case: download(case, plan), plan['cases']))
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), receipts=rows,
                       financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
