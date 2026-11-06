"""Fetch only verified .parquet.zst variants of the originally requested objects."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, build_opener

import pyarrow as pa

from followup import HERE, ROOT, PLAN as FOLLOWUP_PLAN, RESULT as FOLLOWUP_RECEIPTS
from capture_transitions import acquire, OUT, now, sha, verify, write
from capture import NoRedirect, capture

PLAN = HERE/'zstd_registration.json'
RESULT = HERE/'zstd_receipts.json'
PREFIX = '/cryptohftdata-processed/'


def register():
    old = json.loads(FOLLOWUP_PLAN.read_text())
    verify(old)
    manifest = json.loads(FOLLOWUP_RECEIPTS.read_text())
    if manifest['registration_sha256']!=sha(FOLLOWUP_PLAN.read_bytes()):
        raise ValueError('Path audit manifest changed')
    cases = []
    for row in manifest['receipts']:
        if row['source']!='redirect_path_only':
            continue
        path = PREFIX+row['file']+'.zst'
        if (row['http_status'],row['scheme'],row['storage_host'],row['port'],row['path_only']) != (
                302,'https',old['storage_host'],None,path):
            raise ValueError('Refused redirect does not identify the same registered object')
        cases.append(dict(id=row['id'].removesuffix('-path-audit')+'-zstd',group=row['group'],
                          source='collector',symbol=row['symbol'],market=row['market'],day=row['day'],
                          hour=row['hour'],url=row['url'],expected_object_path=path))
    if len(cases)!=18:
        raise ValueError('Unexpected compressed object set')
    inputs = (Path(__file__),FOLLOWUP_PLAN,FOLLOWUP_RECEIPTS)
    write(PLAN,dict(old,registered_at_utc=now(),cases=cases,maximum_requests=2*len(cases),
                     max_decoded_bytes=64*1024**2,
                     input_hashes={**old['input_hashes'],**{
                        str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in inputs}}))


def acquire_zstd(case,plan):
    row = dict(case,started_at_utc=now())
    try:
        try:
            response = build_opener(NoRedirect()).open(
                Request(case['url']),timeout=plan['socket_timeout_seconds'])
        except HTTPError as exc:
            response = exc
        with response:
            row['endpoint_status'] = response.status
            redirect = response.headers.get('Location','')
        url = urlsplit(redirect)
        row['storage_host'] = url.hostname
        if (row['endpoint_status'],url.scheme,url.netloc,unquote(url.path),url.fragment) != (
                302,'https',plan['storage_host'],case['expected_object_path'],''):
            row['state'] = 'redirect_not_admitted'
            return row
        received = capture(dict(case,url=redirect),plan)
        row.update({key:value for key,value in received.items()
                    if key not in ('url','started_at_utc','finished_at_utc')})
        if (row.get('http_status'),row.get('state'))!=(200,'body_complete'):
            return row
        raw = (ROOT/row['path']).read_bytes()
        if raw[:4]!=bytes.fromhex('28b52ffd'):
            raise ValueError('Unexpected Zstandard header')
        cap = plan['max_decoded_bytes']
        with pa.CompressedInputStream(pa.BufferReader(raw),'zstd') as stream:
            decoded = stream.read(cap+1)
        if len(decoded)>cap or not decoded.startswith(b'PAR1') or not decoded.endswith(b'PAR1'):
            raise ValueError('Decoded Parquet is invalid or exceeds cap')
        path = OUT/(case['id']+'.decoded.parquet')
        with path.open('xb') as handle:
            handle.write(decoded)
        row['decoded'] = dict(path=str(path.relative_to(ROOT)),bytes=len(decoded),sha256=sha(decoded))
    except (OSError,ValueError,URLError,pa.ArrowException) as exc:
        row.update(state='decode_or_download_error',error_type=type(exc).__name__)
    finally:
        row['finished_at_utc'] = now()
    return row


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT/(case['id']+'.raw')).exists() for case in plan['cases']):
        raise FileExistsError('Compressed acquisition already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda row:acquire_zstd(row,plan),plan['cases']))
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),receipts=rows,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
