"""Follow one validated storage redirect issued by the anonymous public endpoint."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, build_opener

from capture import HERE, OUT, ROOT, NoRedirect, capture, now, sha, verify, write
from capture_exact import EXACT_PLAN, EXACT_RESULT

PLAN = HERE / 'registration_v3.json'
RESULT = HERE / 'download_receipts.json'
STORAGE_HOST = 'e5de051a8af1332b58a11bd5b2e40258.r2.cloudflarestorage.com'


def register():
    prior = json.loads(EXACT_PLAN.read_text())
    receipts = json.loads(EXACT_RESULT.read_text())
    verify(prior)
    if receipts['registration_sha256'] != sha(EXACT_PLAN.read_bytes()):
        raise ValueError('Corrected registration changed')
    inputs = [Path(__file__), EXACT_PLAN, EXACT_RESULT]
    for row in receipts['receipts']:
        path = ROOT / row['path']
        if (row['http_status'], row['redirect_host']) != (302, STORAGE_HOST):
            raise ValueError('Unexpected prior public redirect')
        if sha(path.read_bytes()) != row['sha256']:
            raise ValueError('Prior redirect response changed')
        inputs.append(path)
    cases = [dict(row, id=row['id'].removesuffix('-exact') + '-download') for row in prior['cases']]
    hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs}
    write(PLAN, dict(prior, registered_at_utc=now(), cases=cases, max_requests=2*len(cases),
                     storage_host=STORAGE_HOST, redirects='one manually validated HTTPS redirect',
                     input_hashes={**prior['input_hashes'], **hashes}))


def download(case, plan):
    result = dict(case, started_at_utc=now())
    try:
        try:
            response = build_opener(NoRedirect()).open(
                Request(case['url']), timeout=plan['socket_timeout_seconds'])
        except HTTPError as exc:
            response = exc
        with response:
            result['endpoint_status'] = response.status
            target = response.headers.get('Location', '')
        url = urlsplit(target)
        result['storage_host'] = url.hostname
        if (result['endpoint_status'] != 302 or url.scheme != 'https'
                or url.netloc != plan['storage_host'] or url.fragment
                or not unquote(url.path).endswith('/' + case['file'])):
            result['state'] = 'redirect_not_admitted'
            return result
        # The transient URL is used solely for its GET. Preserve the public
        # endpoint and host in the receipt, with no storage URL or query.
        acquired = capture(dict(case, url=target), plan)
        result.update({key: value for key, value in acquired.items()
                       if key not in ('url', 'started_at_utc', 'finished_at_utc')})
    except (OSError, ValueError, URLError) as exc:
        result.update(state='download_error', error_type=type(exc).__name__)
    finally:
        result['finished_at_utc'] = now()
    return result


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or any((OUT / (row['id'] + '.raw')).exists() for row in plan['cases']):
        raise FileExistsError('Storage acquisition already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda row: download(row, plan), plan['cases']))
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
