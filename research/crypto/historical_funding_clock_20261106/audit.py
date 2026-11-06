"""Audit collector timestamps against frozen public funding rows and actual quotes."""
from collections import Counter, defaultdict
from decimal import Decimal as D
import json
from pathlib import Path
import sys

import pandas as pd

from capture import BASE, DAY, HERE, OUT, PLAN, ROOT, SHORT_PLAN, SYMBOLS, now, sha, verify, write
from book_replay import Book, nanoseconds
from probe_event_sources import decode

RECEIPTS = HERE / 'capture_receipts.json'
AUDIT_PLAN = HERE / 'audit_registration.json'
NS_PER_HOUR = 3600*10**9


def registration():
    receipts = json.loads(RECEIPTS.read_text())['receipts']
    paths = [Path(__file__), PLAN, RECEIPTS, *(ROOT/r['path'] for r in receipts if 'path' in r)]
    write(AUDIT_PLAN, dict(registered_at_utc=now(), input_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        funding_identity='symbol and exact raw fundingTime milliseconds',
        availability_scope='earliest collector receipt among registered sampled slices',
        order_book_policy='sequence replay only from actually captured snapshots',
        new_financial_accounts=0))


def messages(receipt):
    start = nanoseconds(receipt['day']+'T00:00:00Z')+receipt['offset_minutes']*60*10**9
    previous = start
    body = decode((ROOT/receipt['path']).read_bytes(), receipt['headers'])
    for line_number,line in enumerate(body.decode().splitlines(),1):
        if not line.strip():
            yield line_number,None,None,None
            continue
        local,raw = line.split(' ',1)
        at = nanoseconds(local)
        if not previous <= at < start+60*10**9:
            raise ValueError('Collector timestamp regressed or left its registered slice')
        previous = at
        yield line_number,local,at,json.loads(raw)


def frozen_rates():
    base = ROOT/json.loads(SHORT_PLAN.read_text())['runs'][0]['datadir']/'futures'
    raw, native = {}, {}
    for symbol in SYMBOLS:
        rows = json.loads((BASE/(symbol+'.json')).read_text())['body']
        for row in rows:
            key = (symbol,row['fundingTime'])
            if key in raw or row['symbol'] != symbol:
                raise ValueError('Duplicate or misbound frozen funding record')
            raw[key] = row
        frame = pd.read_feather(base/f'{symbol[:-4]}_USDT_USDT-1h-funding_rate.feather')
        if frame.date.duplicated().any():
            raise ValueError('Duplicate native funding hour')
        native[symbol] = {t.value:D(str(r)) for t,r in zip(frame.date,frame.funding_rate)}
    return raw,native


def funding_rows(receipts, raw, native):
    observed = defaultdict(list)
    for receipt in receipts:
        for line,local,at,message in messages(receipt):
            if message is None:
                continue
            data = message['data']
            symbol, time_ms = data['symbol'],data['fundingTime']
            key = symbol,time_ms
            if (symbol not in SYMBOLS or type(time_ms) is not int or at < time_ms*10**6
                    or message['generated'] is not True or message['stream'] != symbol.lower()+'@fundingRate'):
                raise ValueError('Unexpected funding sample identity or chronology')
            fields = ('symbol','fundingTime','fundingRate','markPrice','rateType')
            exact = key in raw and all(data.get(k)==raw[key].get(k) for k in fields)
            native_hour = time_ms*10**6//NS_PER_HOUR*NS_PER_HOUR
            native_value = native[symbol].get(native_hour)
            observed[key].append(dict(source_slice=receipt['id'],source_line=line,collector_received_at=local,
                collector_received_ns=at, raw=data, exact_frozen_public_fields_match=exact,
                frozen_native_hour_ns=native_hour, native_rate_matches=(D(data['fundingRate'])==native_value),
                settlement_after_native_hour_ms=time_ms-native_hour//10**6))
    result = []
    for (symbol,time_ms),rows in sorted(observed.items()):
        received = rows[0]['collector_received_ns']
        if any(row['raw'] != rows[0]['raw'] for row in rows):
            raise ValueError('Repeated source funding identity changed its payload')
        result.append(dict(symbol=symbol,settlement_time_ms=time_ms,first_sampled_receive_at=rows[0]['collector_received_at'],
            first_sampled_receive_ns=received,seconds_after_settlement=str(D(received-time_ms*10**6)/10**9),
            observations=rows))
    return result


def book_rows(receipt, funding):
    books = {s:Book('futures') for s in SYMBOLS}
    counts = Counter()
    results = {s:dict(snapshots=0,book_observations=0,both_sides_20bps_complete=0,
                      book_errors=[],ticker_receipts=0,ticker_exchange_E_after_collector=0) for s in SYMBOLS}
    first_funding = {f['symbol']:f['first_sampled_receive_ns'] for f in reversed(funding)}
    latest_ticker = {}
    source = OUT/'normalized_bbo.jsonl'
    with source.open('x') as output:
        for line,local,at,message in messages(receipt):
            if message is None:
                counts['disconnect'] += 1
                for book in books.values():
                    book.reset()
                continue
            symbol,channel = message['stream'].split('@',1)
            symbol = symbol.upper()
            data = message['data']
            info = results[symbol]
            book = books[symbol]
            counts[message['stream']] += 1
            if channel == 'bookTicker':
                if (data['s']!=symbol or type(data['E']) is not int or type(data['u']) is not int
                        or not D(0)<D(data['b'])<D(data['a']) or min(D(data['B']),D(data['A']))<=0):
                    raise ValueError('Invalid independent quote receipt')
                quote = dict(symbol=symbol,source_line=line,collector_received_at=local,collector_received_ns=at,
                    exchange_event_ms=data['E'],exchange_transaction_ms=data['T'],update_id=data['u'],
                    bid=data['b'],ask=data['a'],bid_quantity=data['B'],ask_quantity=data['A'])
                info['ticker_receipts'] += 1
                info.setdefault('first_ticker',quote)
                info['last_ticker'] = quote
                clock_consistent = data['E']*10**6 <= at
                info['ticker_exchange_E_after_collector'] += int(not clock_consistent)
                if clock_consistent and at <= first_funding.get(symbol,-1):
                    latest_ticker[symbol] = quote
                output.write(json.dumps(quote)+'\n')
            if channel != 'depthSnapshot' and not channel.startswith('depth@'):
                continue
            try:
                if channel == 'depthSnapshot':
                    info['snapshots'] += 1
                    book.snapshot(data)
                elif not book.failed:
                    book.update(data)
                observation = book.observation()
                if observation is not None:
                    info['book_observations'] += 1
                    info['both_sides_20bps_complete'] += int(observation['bid_band_complete'] and observation['ask_band_complete'])
            except ValueError as exc:
                info['book_errors'].append(dict(source_line=line,error=str(exc)))
                book.failed = True
    for symbol,info in results.items():
        info['latest_causally_ordered_ticker_at_first_sampled_funding_receive'] = latest_ticker.get(symbol)
        info['unapplied_depth_updates_without_snapshot'] = len(books[symbol].buffer)
    return dict(stream_counts=dict(counts),symbols=results,normalized_bbo=str(source.relative_to(ROOT)),
                normalized_bbo_sha256=sha(source.read_bytes()),raw_capture_sha256=receipt['sha256'])


def run():
    plan = json.loads(PLAN.read_text())
    audit = json.loads(AUDIT_PLAN.read_text())
    verify(plan)
    verify(audit)
    captures = json.loads(RECEIPTS.read_text())
    if captures['registration_sha256']!=sha(PLAN.read_bytes()):
        raise ValueError('Capture receipt registration drift')
    receipts = captures['receipts']
    for r in receipts:
        if 'path' in r and sha((ROOT/r['path']).read_bytes())!=r['sha256']:
            raise ValueError('Captured payload hash mismatch')
    accepted = [r for r in receipts if r.get('http_status')==200 and r['response_state']=='body_complete']
    funding = funding_rows([r for r in accepted if r['channels']==['fundingRate']], *frozen_rates())
    source_books = [book_rows(r,funding) for r in accepted if 'depth' in r['channels']]
    verify(plan)
    verify(audit)
    write(HERE/'audit_result.json',dict(completed_at_utc=now(),audit_registration_sha256=sha(AUDIT_PLAN.read_bytes()),
        funding=funding, book_samples=source_books, economic_admission=False, financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        registration()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
