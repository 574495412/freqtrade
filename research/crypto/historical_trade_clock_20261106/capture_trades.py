"""Register paired public trade downloads for already sampled frozen dates."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / 'historical_l2_hourly_20261106'
sys.path.insert(0, str(PRIOR))
from capture import ENDPOINT, OUT, ROOT, capture, now, sha, verify, write
from download import RESULT as L2_RECEIPTS, PLAN as L2_PLAN, download

PLAN = HERE / 'capture_registration.json'
RESULT = HERE / 'capture_receipts.json'
SPOT_L = HERE.parent / 'factor_cost_long_20261106/plan.json'
FUTURES_L = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
FUTURES_S = HERE.parent / 'factor_cost_futures_20261106/plan.json'


def register():
    l2 = json.loads(L2_PLAN.read_text())
    verify(l2)
    manifest = json.loads(L2_RECEIPTS.read_text())
    if manifest['registration_sha256'] != sha(L2_PLAN.read_bytes()):
        raise ValueError('Prior L2 manifest changed')
    plans = {('spot', 'L'): SPOT_L, ('futures', 'L'): FUTURES_L, ('futures', 'S'): FUTURES_S}
    cases = []
    frozen_candles = {}
    for row in manifest['receipts']:
        market, window, day, symbol = (row[k] for k in ('market', 'window', 'day', 'symbol'))
        if (market, window) not in plans:
            continue
        if row['http_status'] != 200:
            raise ValueError('Prior L2 comparison hour unavailable')
        key = f'{market}-{day}-{symbol}'
        hour = row['hour']
        file = f'binance_{market}/{day}/{hour:02d}/{symbol}_trades.parquet'
        path = 'spot' if market == 'spot' else 'futures/um'
        official = f'https://data.binance.vision/data/{path}/daily/trades/{symbol}/{symbol}-trades-{day}.zip'
        base = dict(group=key, day=day, hour=hour, market=market, window=window, symbol=symbol)
        cases.extend((
            dict(base, id=f'trade-{key}-hour', source='collector', file=file,
                 url=ENDPOINT + '?' + urlencode({'file': file})),
            dict(base, id=f'trade-{key}-zip', source='official_zip', url=official),
            dict(base, id=f'trade-{key}-checksum', source='official_checksum', url=official + '.CHECKSUM'),
        ))
        run = json.loads(plans[market, window].read_text())['runs'][0]
        datadir = ROOT / run['datadir']
        base_symbol = symbol.removesuffix('USDT')
        candle = (datadir / f'{base_symbol}_USDT-5m.feather' if market == 'spot'
                  else datadir / 'futures' / f'{base_symbol}_USDT_USDT-5m-futures.feather')
        if not run['start'][:10] <= day < run['end'][:10] or not candle.is_file():
            raise ValueError('Window or frozen candle source mismatch')
        frozen_candles[key] = str(candle.relative_to(ROOT))
    inputs = [Path(__file__), L2_PLAN, L2_RECEIPTS, *plans.values(),
              *(ROOT / p for p in frozen_candles.values())]
    hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs}
    write(PLAN, dict(registered_at_utc=now(), cases=cases, frozen_candles=frozen_candles,
                     workers=2, max_requests=len(cases) + len(frozen_candles), retries=0,
                     socket_timeout_seconds=l2['socket_timeout_seconds'],
                     max_bytes_per_response=l2['max_bytes_per_response'],
                     storage_host=l2['storage_host'],
                     input_hashes={**l2['input_hashes'], **hashes},
                     scope='Compare same individual trade identity to exact official daily rows; '
                           'measure independent collector receipt and frozen 5m OHLCV parity.',
                     financial_accounts=0))


def acquire(case, plan):
    if case['source'] == 'collector':
        return download(case, plan)
    if case['source'] == 'official_checksum':
        plan = dict(plan, max_bytes_per_response=4096)
    return capture(case, plan)


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT / (case['id'] + '.raw')).exists() for case in plan['cases']):
        raise FileExistsError('Trade acquisition already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda case: acquire(case, plan), plan['cases']))
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
