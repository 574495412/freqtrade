"""Check the refused public object identities and two independent DYDX archive types."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, build_opener

from capture_transitions import HERE, ROOT, PLAN as ORIGINAL_PLAN, RESULT as ORIGINAL_RECEIPTS
from capture_transitions import acquire, now, sha, verify, write
from capture import NoRedirect

PLAN = HERE/'followup_registration.json'
RESULT = HERE/'followup_receipts.json'


def register():
    original = json.loads(ORIGINAL_PLAN.read_text())
    verify(original)
    manifest = json.loads(ORIGINAL_RECEIPTS.read_text())
    if manifest['registration_sha256']!=sha(ORIGINAL_PLAN.read_bytes()):
        raise ValueError('Original capture registration mismatch')
    cases = [dict(row,id=row['id']+'-path-audit',source='redirect_path_only')
             for row in original['cases'] if any(
                 x['id']==row['id'] and x.get('state')=='redirect_not_admitted' for x in manifest['receipts'])]
    if len(cases)!=18:
        raise ValueError('Unexpected refused object set')
    base = 'https://data.binance.vision/data/futures/um/daily/'
    for kind,segment in [('aggTrades','aggTrades/DYDXUSDT'),('klines','klines/DYDXUSDT/5m')]:
        middle = '5m' if kind=='klines' else kind
        filename = f'DYDXUSDT-{middle}-2026-04-16.zip'
        for source,suffix in [('official_zip',''),('official_checksum','.CHECKSUM')]:
            cases.append(dict(id=f'tick-DYDXUSDT-{kind}-{source}',source=source,kind=kind,
                              url=f'{base}{segment}/{filename}{suffix}'))
    inputs = [Path(__file__),ORIGINAL_PLAN,ORIGINAL_RECEIPTS]
    write(PLAN,dict(original,registered_at_utc=now(),cases=cases,maximum_requests=len(cases),
                     scope='Public redirect path identity only; checksummed aggTrades and 5m DYDX archives.',
                     input_hashes={**original['input_hashes'],**{
                        str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in inputs}}))


def inspect(case,plan):
    if case['source']!='redirect_path_only':
        return acquire(case,plan)
    row = dict(case,started_at_utc=now())
    try:
        try:
            response = build_opener(NoRedirect()).open(
                Request(case['url']),timeout=plan['socket_timeout_seconds'])
        except HTTPError as exc:
            response = exc
        with response:
            url = urlsplit(response.headers.get('Location',''))
            row.update(http_status=response.status,storage_host=url.hostname,
                       path_only=unquote(url.path),scheme=url.scheme,port=url.port,
                       expected_key_matches=unquote(url.path).endswith('/'+case['file']))
    except (OSError,ValueError,URLError) as exc:
        row['error_type']=type(exc).__name__
    row['finished_at_utc']=now()
    return row


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists():
        raise FileExistsError('Follow-up already completed')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda row:inspect(row,plan),plan['cases']))
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),receipts=rows,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
