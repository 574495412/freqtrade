"""Bounded anonymous downloads from the documented hourly L2 endpoint."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = HERE.parent / 'rd_program_20261105/data_extension'
sys.path.insert(0, str(DATA))
from probe_event_sources import now, sha, write

OUT = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1/hourly_l2_v001'
PLAN = HERE / 'registration.json'
RESULT = HERE / 'capture_receipts.json'
ENDPOINT = 'https://api.cryptohftdata.com/v1/download'
CASES = (
    ('spot', 'L', '2026-08-31', 'BTCUSDT'),
    ('futures', 'L', '2026-08-31', 'BTCUSDT'),
    ('spot', 'S', '2026-11-01', 'BTCUSDT'),
    ('futures', 'S', '2026-11-01', 'BTCUSDT'),
    ('futures', 'S', '2026-11-01', 'SOLUSDT'),
)
HEADERS = {'content-type', 'content-encoding', 'content-length', 'etag', 'last-modified', 'retry-after'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify(plan):
    for path, expected in plan['input_hashes'].items():
        if sha((ROOT / path).read_bytes()) != expected:
            raise ValueError('Input changed: ' + path)


def register():
    roadmap = HERE.parent / 'rd_program_20261105/plan.json'
    frozen = json.loads(roadmap.read_text())
    windows = {
        'L': HERE.parent / 'factor_cost_futures_long_20261106/plan.json',
        'S': HERE.parent / 'factor_cost_futures_20261106/plan.json',
    }
    cases = []
    for market, window, day, symbol in CASES:
        interval = json.loads(windows[window].read_text())['runs'][0]
        if not interval['start'][:10] <= day < interval['end'][:10]:
            raise ValueError('Sample falls outside frozen interval')
        if symbol not in {pair.replace('/', '').split(':')[0] for pair in frozen['markets'][market]['pairs']}:
            raise ValueError('Sample falls outside frozen universe')
        file = f'binance_{market}/{day.replace("-", "/")}/00/{symbol}_orderbook.parquet'
        cases.append(dict(id=f'{market}-{day}-{symbol}', market=market, window=window, day=day,
                          hour=0, symbol=symbol, file=file, url=ENDPOINT + '?' + urlencode({'file': file})))
    inputs = (Path(__file__), DATA / 'probe_event_sources.py', roadmap, *windows.values())
    write(PLAN, dict(registered_at_utc=now(), cases=cases, workers=2, max_requests=len(cases),
                     socket_timeout_seconds=30, max_bytes_per_response=128 * 1024**2, redirects=False,
                     retries=0, financial_accounts=0,
                     input_hashes={str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs},
                     scope='Window-matched sample access and schema probe. Public GET; no credentials.'))


def capture(case, plan):
    result = dict(case, started_at_utc=now())
    try:
        req = Request(case['url'], headers={'User-Agent': 'bounded-research-schema-probe',
                                           'Accept-Encoding': 'identity'})
        try:
            response = build_opener(NoRedirect()).open(req, timeout=plan['socket_timeout_seconds'])
        except HTTPError as exc:
            response = exc
        with response:
            headers = {key.lower(): value for key, value in response.headers.items()
                       if key.lower() in HEADERS}
            result.update(http_status=response.status, headers=headers)
            if response.headers.get('Location'):
                result['redirect_host'] = urlsplit(response.headers['Location']).hostname
            cap = plan['max_bytes_per_response'] if response.status == 200 else 128 * 1024
            length = headers.get('content-length')
            if length is not None and int(length) > cap:
                result['state'] = 'declared_body_exceeds_cap'
                return result
            path = OUT / (case['id'] + '.raw')
            digest, total = sha256(), 0
            try:
                with path.open('xb') as handle:
                    while total <= cap:
                        chunk = response.read(min(1024**2, cap + 1 - total))
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
            finally:
                result.update(path=str(path.relative_to(ROOT)), bytes=total, sha256=digest.hexdigest())
            if total > cap:
                result['state'] = 'truncated_at_cap'
            elif length is not None and total != int(length):
                result['state'] = 'content_length_mismatch'
            else:
                result['state'] = 'body_complete'
    except (OSError, ValueError, URLError) as exc:
        result.update(error_type=type(exc).__name__, state='download_error')
    finally:
        result['finished_at_utc'] = now()
    return result


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    OUT.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda case: capture(case, plan), plan['cases']))
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), receipts=rows,
                       economic_admission=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
