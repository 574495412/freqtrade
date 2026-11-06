"""Register the documented date segment while preserving the original request receipts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
from urllib.parse import urlencode

from capture import ENDPOINT, HERE, OUT, PLAN, RESULT, ROOT, capture, now, sha, verify, write

EXACT_PLAN = HERE / 'registration_v2.json'
EXACT_RESULT = HERE / 'capture_receipts_v2.json'


def register():
    prior = json.loads(PLAN.read_text())
    verify(prior)
    receipts = json.loads(RESULT.read_text())
    if receipts['registration_sha256'] != sha(PLAN.read_bytes()):
        raise ValueError('Prior request registration changed')
    cases = []
    inputs = [Path(__file__), PLAN, RESULT]
    for row in receipts['receipts']:
        path = ROOT / row['path']
        if row['http_status'] != 404 or sha(path.read_bytes()) != row['sha256']:
            raise ValueError('Unexpected prior capture result')
        inputs.append(path)
    for case in prior['cases']:
        file = f"binance_{case['market']}/{case['day']}/{case['hour']:02d}/{case['symbol']}_orderbook.parquet"
        cases.append(dict(case, id=case['id'] + '-exact', file=file,
                          url=ENDPOINT + '?' + urlencode({'file': file})))
    hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in inputs}
    write(EXACT_PLAN, dict(prior, registered_at_utc=now(), cases=cases,
                           input_hashes={**prior['input_hashes'], **hashes},
                           correction='Use a single YYYY-MM-DD path segment as in the provider docs.'))


def run():
    plan = json.loads(EXACT_PLAN.read_text())
    verify(plan)
    if EXACT_RESULT.exists() or any((OUT / (row['id'] + '.raw')).exists() for row in plan['cases']):
        raise FileExistsError('Corrected acquisition already started')
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        rows = list(pool.map(lambda case: capture(case, plan), plan['cases']))
    verify(plan)
    write(EXACT_RESULT, dict(registration_sha256=sha(EXACT_PLAN.read_bytes()), receipts=rows,
                             economic_admission=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
