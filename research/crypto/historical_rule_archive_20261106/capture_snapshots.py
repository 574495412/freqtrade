"""Retrieve exact public archive captures and validate point-in-time rule fields."""
from base64 import b32encode
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha1
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from probe_index import CAP, EXPANDED, HELPER, HERE, ORIGINS, OUT, ROOT
from probe_index import decode, now, sha, verify, write

INDEX = HERE / 'index_result.json'
REGISTRATION = HERE / 'snapshot_registration.json'
RESULT = HERE / 'snapshot_result.json'
ROADMAP = HERE.parent / 'rd_program_20261105/plan.json'
MARKETS = {
    'spot': HERE.parent / 'spot_baseline_20261105/public-spot-market-rules.json',
    'futures': HERE.parent / 'strategy_comparison_wide_20261105/public-market-rules-40.json',
}
HEADERS = {
    'content-type', 'content-length', 'content-encoding', 'location', 'memento-datetime',
    'x-archive-orig-date', 'x-archive-orig-content-type',
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def register():
    index = json.loads(INDEX.read_text())
    index_registration = HERE / 'registration.json'
    if sha(index_registration.read_bytes()) != index['registration_sha256']:
        raise ValueError('Index registration hash mismatch')
    verify(json.loads(index_registration.read_text()))
    cases = []
    for row in index['rows']:
        if 'sha256' in row and sha((ROOT / row['path']).read_bytes()) != row['sha256']:
            raise ValueError('Raw index body hash mismatch')
        market = row['market']
        for capture in row.get('index_records', []):
            if (capture['original'] == ORIGINS[market] and row['window'] in capture['inside_windows']
                    and capture['status'] == '200' and capture['mimetype'] == 'application/json'):
                stamp = capture['timestamp']
                cases.append(dict(capture, id=f'{market}-{stamp}', market=market, window=row['window'],
                                  url=f'https://web.archive.org/web/{stamp}id_/{ORIGINS[market]}'))
    if len(cases) != 5 or len({row['id'] for row in cases}) != len(cases):
        raise ValueError('Unexpected exact public capture set')

    roadmap = json.loads(ROADMAP.read_text())
    universe = {}
    for market, source in MARKETS.items():
        frozen = json.loads(source.read_text())['markets']
        id_key = 'symbol' if market == 'spot' else 'id'
        universe[market] = {pair: info[id_key] for pair, info in frozen.items()}
        if (set(frozen) != set(roadmap['markets'][market]['pairs'])
                or len(set(universe[market].values())) != len(frozen)):
            raise ValueError('Frozen market universe mismatch')
    inputs = [Path(__file__), HERE / 'probe_index.py', HELPER, INDEX, index_registration,
              ROADMAP, *MARKETS.values()]
    write(REGISTRATION, dict(
        registered_at_utc=now(), cases=cases, universe=universe, maximum_requests=len(cases),
        workers=3, timeout_seconds=30, retries=0, redirects=False, raw_cap_bytes=CAP,
        expanded_cap_bytes=EXPANDED, financial_accounts=0,
        input_hashes={str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs},
        scope='Exact CDX-listed captures, point observations only. No inferred effective intervals.',
    ))


def fetch(url, timeout):
    request = Request(url, headers={'Accept-Encoding': 'gzip',
                                    'User-Agent': 'bounded-research-schema-probe'})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        headers = {key.lower(): value for key, value in response.headers.items()
                   if key.lower() in HEADERS}
        if int(headers.get('content-length', '0')) > CAP:
            return response.status, response.geturl(), headers, None, 'declared_body_exceeds_cap'
        body = response.read(CAP + 1)
        state = 'body_complete' if len(body) <= CAP else 'truncated_at_cap'
        return response.status, response.geturl(), headers, body, state


def fields(symbol, market):
    filters = {item['filterType']: item for item in symbol['filters']}
    if len(filters) != len(symbol['filters']):
        raise ValueError('Duplicate filter')
    notional_type = 'MIN_NOTIONAL' if 'MIN_NOTIONAL' in filters else 'NOTIONAL'
    notional_key = 'notional' if market == 'futures' else 'minNotional'
    raw = {
        'tickSize': filters['PRICE_FILTER']['tickSize'],
        'minPrice': filters['PRICE_FILTER']['minPrice'],
        'maxPrice': filters['PRICE_FILTER']['maxPrice'],
        'stepSize': filters['LOT_SIZE']['stepSize'],
        'minQty': filters['LOT_SIZE']['minQty'],
        'maxQty': filters['LOT_SIZE']['maxQty'],
        'minNotional': filters[notional_type][notional_key],
    }
    numbers = {key: Decimal(str(value)) for key, value in raw.items()}
    if any(not value.is_finite() or value < 0 for value in numbers.values()):
        raise ValueError('Invalid rule value')
    if not all(numbers[key] > 0 for key in ('tickSize', 'stepSize')):
        raise ValueError('Missing positive increment')
    if not isinstance(symbol['status'], str) or not symbol['status']:
        raise ValueError('Missing instrument status')
    return dict(raw, status=symbol['status'], notional_filter=notional_type,
                market_lot_size=filters.get('MARKET_LOT_SIZE'))


def inspect(body, market, universe):
    payload = json.loads(body)
    if not isinstance(payload.get('symbols'), list) or not payload['symbols']:
        raise ValueError('Missing exchangeInfo symbols')
    symbols = {row['symbol']: row for row in payload['symbols']}
    if len(symbols) != len(payload['symbols']):
        raise ValueError('Duplicate symbol in exchangeInfo')
    complete, missing, invalid = {}, [], {}
    for pair, symbol_id in universe.items():
        row = symbols.get(symbol_id)
        if row is None:
            missing.append(pair)
            continue
        try:
            base, _ = pair.split('/')
            if row['baseAsset'] != base or row['quoteAsset'] != 'USDT':
                raise ValueError('Base or quote does not match')
            if market == 'futures' and (row['contractType'], row['marginAsset']) != ('PERPETUAL', 'USDT'):
                raise ValueError('Contract identity does not match')
            complete[pair] = fields(row, market)
        except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
            invalid[pair] = f'{type(exc).__name__}: {exc}'
    return dict(payload_symbols=len(symbols), payload_server_time=payload.get('serverTime'),
                complete_rules=complete, missing_pairs=missing, invalid_pairs=invalid)


def probe(case, plan):
    result = dict(case, started_at_utc=now(), archive_point_verified=False)
    try:
        status, effective_url, headers, raw, state = fetch(case['url'], plan['timeout_seconds'])
        result.update(http_status=status, effective_url=effective_url, headers=headers, response_state=state)
        if raw is not None:
            path = OUT / (case['id'] + '-exchangeInfo.raw')
            with path.open('xb') as handle:
                handle.write(raw)
            result.update(path=str(path.relative_to(ROOT)), bytes=len(raw), sha256=sha(raw))
        if status != 200 or effective_url != case['url'] or state != 'body_complete':
            return result
        body = decode(raw, headers)
        if 'json' not in headers.get('content-type', '').lower():
            raise ValueError('Replay content type is not JSON')
        memento = headers.get('memento-datetime')
        memento_matches = bool(memento and parsedate_to_datetime(memento).astimezone(timezone.utc)
                               .strftime('%Y%m%d%H%M%S') == case['timestamp'])
        digests = {name: b32encode(sha1(data).digest()).decode()
                   for name, data in [('raw', raw), ('decoded', body)]}
        result.update(memento_matches=memento_matches, computed_base32_sha1=digests,
                      cdx_digest_matches=case['digest'] in digests.values())
        if (memento and not memento_matches) or not result['cdx_digest_matches']:
            raise ValueError('Archive time or CDX digest did not match the registered capture')
        result.update(inspect(body, case['market'], plan['universe'][case['market']]))
        result['archive_point_verified'] = bool(result['complete_rules'])
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['finished_at_utc'] = now()
    return result


def run():
    plan = json.loads(REGISTRATION.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT / (row['id'] + '-exchangeInfo.raw')).exists() for row in plan['cases']):
        raise FileExistsError('Snapshot acquisition has already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as workers:
        rows = list(workers.map(lambda case: probe(case, plan), plan['cases']))
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(REGISTRATION.read_bytes()), rows=rows,
                       historical_rule_admission=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
