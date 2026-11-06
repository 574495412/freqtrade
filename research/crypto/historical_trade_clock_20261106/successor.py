"""Check the next collector hour for the two unmatched long-window boundary trades."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from audit_trades import PLAN as AUDIT_PLAN, RESULT as AUDIT_RESULT, collector, official_rows, start_ns
from capture_trades import HERE, ROOT, OUT, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture import ENDPOINT, now, sha, verify, write
from download import download

PLAN = HERE / 'successor_registration.json'
RESULT = HERE / 'successor_result.json'
GROUP = 'futures-2026-08-31-BTCUSDT'


def register():
    original = json.loads(CAPTURE_PLAN.read_text())
    audit_plan = json.loads(AUDIT_PLAN.read_text())
    verify(audit_plan)
    audit = json.loads(AUDIT_RESULT.read_text())
    if audit['registration_sha256'] != sha(AUDIT_PLAN.read_bytes()):
        raise ValueError('Original trade audit changed')
    target = next(row for row in audit['results'] if row['id'] == GROUP)
    if target['join_counts']['missing_collector_id'] != 2 or target['bars'][-1]['complete_observed_constituents']:
        raise ValueError('Unexpected target boundary scope')
    receipts = {row['source']: row for row in json.loads(CAPTURE_RESULT.read_text())['receipts']
                if row['group'] == GROUP}
    hour = receipts['collector']
    before, _ = collector(hour)
    missing = []
    for tid, traded, price, qty, maker in official_rows(
            receipts['official_zip'], receipts['official_checksum'], audit_plan, Counter()):
        if tid not in before:
            missing.append(dict(trade_id=tid, exchange_trade_ns=traded, price=str(price),
                                quantity=str(qty), is_buyer_maker=maker))
    if len(missing) != 2 or any(row['exchange_trade_ns'] < target['bars'][-1]['start_ns'] for row in missing):
        raise ValueError('Missing trades are not exactly the registered last-bar observations')
    file = f"binance_futures/{hour['day']}/01/{hour['symbol']}_trades.parquet"
    case = {key: hour[key] for key in ('group', 'day', 'market', 'window', 'symbol', 'source')}
    case.update(id=hour['id'] + '-successor', hour=1, file=file, url=ENDPOINT + '?' + urlencode({'file': file}))
    hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes())
              for path in (Path(__file__), AUDIT_PLAN, AUDIT_RESULT)}
    write(PLAN, dict(registered_at_utc=now(), case=case, missing=missing,
                     max_requests=2, retries=0, financial_accounts=0,
                     socket_timeout_seconds=original['socket_timeout_seconds'],
                     max_bytes_per_response=original['max_bytes_per_response'],
                     storage_host=original['storage_host'],
                     input_hashes={**audit_plan['input_hashes'], **hashes}))


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or (OUT / (plan['case']['id'] + '.raw')).exists():
        raise FileExistsError('Successor request already started')
    receipt = download(plan['case'], plan)
    matches = []
    if (receipt.get('http_status'), receipt.get('state')) == (200, 'body_complete'):
        observed, _ = collector(receipt)
        for trade in plan['missing']:
            values = observed.get(trade['trade_id'])
            if values is None:
                continue
            price, quantity, traded, maker, event, received = values
            if (price == Decimal(trade['price']) and quantity == Decimal(trade['quantity'])
                    and traded*10**6 == trade['exchange_trade_ns'] and maker == trade['is_buyer_maker']
                    and traded <= event <= received//10**6):
                matches.append(dict(trade, collector_received_ns=received, exchange_event_ms=event,
                                    receipt_after_hour_boundary_ns=received-start_ns(receipt)))
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), receipt=receipt, recovered=matches,
                       exchange_kline_publication_verified=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
