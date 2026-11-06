"""Reconstruct frozen pair books at two cutoffs with explicit source clock and depth gates."""
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys

from capture_universe import HERE, ROOT, DATA, FROZEN, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture_universe import now, sha, verify, write
from book_replay import Book, nanoseconds
from probe_event_sources import decode

PLAN = HERE/'audit_registration.json'
RESULT = HERE/'audit_result.json'
SECOND = 10**9
MINUTE = 60*SECOND


def register():
    plan = json.loads(CAPTURE_PLAN.read_text())
    manifest = json.loads(CAPTURE_RESULT.read_text())
    verify(plan)
    if manifest['registration_sha256'] != sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration hash mismatch')
    paths = [Path(__file__), CAPTURE_PLAN, CAPTURE_RESULT, DATA/'book_replay.py']
    for row in manifest['receipts']:
        if 'path' in row:
            path = ROOT/row['path']
            if sha(path.read_bytes()) != row['sha256']:
                raise ValueError('Capture body changed')
            paths.append(path)
    write(PLAN,dict(registered_at_utc=now(),
                    input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in paths},
                    exact_cutoff='00:05:00 UTC inclusive in collector receive order',
                    event_time='Spot before 2025-02-28 milliseconds; later spot microseconds; futures milliseconds.',
                    admission_scope='Pair-point only. Both bands inside an actual snapshot boundary; '
                                    'book sequence and event/receive freshness independently required.',
                    financial_accounts=0))


def timeline(row):
    if row.get('http_status') != 200 or row.get('response_state') != 'body_complete':
        raise ValueError(f"Source response unavailable ({row.get('http_status')}, {row.get('response_state')})")
    start = nanoseconds(row['day']+'T00:00:00Z') + row['minute']*MINUTE
    end = start + row['width']*MINUTE
    path = ROOT/row['path']
    body = decode(path.read_bytes(), row['headers'])
    previous = start
    for line_no, line in enumerate(body.decode().splitlines(),1):
        if not line.strip():
            yield line_no, previous, None
            continue
        local, raw = line.split(' ',1)
        received = nanoseconds(local)
        if not previous <= received < end:
            raise ValueError(f'Receive time out of order or slice at line {line_no}')
        previous = received
        message = json.loads(raw)
        symbol, channel = message['stream'].split('@',1)
        if symbol not in row['symbols']:
            raise ValueError('Out-of-scope symbol')
        yield line_no, received, (symbol, channel, message)


def exchange_ns(data, row, received, *, required):
    event = data.get('E')
    if event is None and not required:
        return None
    unit = 1000 if row['market']=='spot' and row['day']>='2025-02-28' else 10**6
    if type(event) is not int or event <= 0 or event*unit > received:
        raise ValueError('Exchange event timestamp missing or after receipt')
    if row['market']=='futures':
        traded = data.get('T')
        if type(traded) is not int or not 0 < traded <= event:
            raise ValueError('Exchange transaction time missing or after event')
    return event*unit


def inspect_depth(row, cutoff):
    books = {symbol:Book(row['market']) for symbol in row['symbols']}
    records = {symbol:dict(counts=Counter(), last_update=None, anchor=None, observation=None, errors=[])
               for symbol in row['symbols']}
    for line, received, parsed in timeline(row):
        if received > cutoff:
            continue
        if parsed is None:
            # Untimed disconnect with preceding receipt at/before cutoff.
            # Discard the prior state even if the next receipt is after cutoff.
            for symbol, book in books.items():
                book.reset()
                records[symbol]['last_update'] = records[symbol]['anchor'] = None
                records[symbol]['counts']['disconnect'] += 1
            continue
        symbol, channel, message = parsed
        data = message['data']
        book = books[symbol]
        state = records[symbol]
        try:
            if channel == 'depthSnapshot':
                if message.get('generated') is not True or type(data['lastUpdateId']) is not int:
                    raise ValueError('Depth anchor lacks captured REST snapshot identity')
                state['counts']['snapshots'] += 1
                book.snapshot(data)
                state['anchor'] = dict(sequence=data['lastUpdateId'], source_line=line,
                                       received_ns=received, bid_boundary=str(book.bid_boundary),
                                       ask_boundary=str(book.ask_boundary))
                # Buffered predecessor updates may bridge the REST snapshot,
                # but their old message clock cannot describe this receipt.
                state['last_update'] = None
            elif channel.startswith('depth@'):
                if data.get('s','').lower()!=symbol:
                    raise ValueError('Depth update symbol mismatch')
                state['counts']['increments'] += 1
                event = exchange_ns(data,row,received,required=True)
                if book.update(data):
                    state['counts']['applied_after_anchor'] += 1
                    state['last_update'] = dict(sequence=data['u'], source_line=line,
                                               event_ns=event, received_ns=received)
            else:
                raise ValueError('Unexpected depth channel')
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            book.reset()
            state['last_update'] = state['anchor'] = None
            state['counts']['invalidated'] += 1
            if len(state['errors']) < 5:
                state['errors'].append(dict(source_line=line, reason=str(exc)))
    for symbol, book in books.items():
        state = records[symbol]
        if state['last_update'] is not None:
            try:
                state['observation'] = book.observation()
            except ValueError as exc:
                state['errors'].append(dict(source_line=state['last_update']['source_line'], reason=str(exc)))
    return records


def inspect_bbo(row, cutoff):
    quotes = defaultdict(dict)
    counts = defaultdict(Counter)
    for line, received, parsed in timeline(row):
        if parsed is None or received > cutoff:
            continue
        symbol, channel, message = parsed
        data = message['data']
        if channel != 'bookTicker' or data.get('s','').lower() != symbol:
            raise ValueError('Unexpected BBO channel or symbol')
        counts[symbol]['messages'] += 1
        try:
            bid, ask, bq, aq = (Decimal(data[key]) for key in ('b','a','B','A'))
            seq = data['u']
            if type(seq) is not int or not 0 < bid < ask or min(bq,aq) <= 0:
                raise ValueError('Invalid BBO')
            event = exchange_ns(data,row,received,required=row['market']=='futures')
        except (ValueError, KeyError, TypeError, ArithmeticError):
            counts[symbol]['invalid'] += 1
            continue
        quote = dict(sequence=seq, bid=str(bid), ask=str(ask), received_ns=received,
                     event_ns=event, source_line=line)
        old = quotes[symbol].get(seq)
        if seq in quotes[symbol] and (old is None or (old['bid'],old['ask']) != (quote['bid'],quote['ask'])):
            counts[symbol]['conflicting_same_id'] += 1
            quotes[symbol][seq] = None
        else:
            quotes[symbol][seq] = quote
    return quotes, counts


def summarize(group, cutoff, freshness):
    drow, qrow = group['depth'], group['bbo']
    errors = {}
    depth, quotes, counts = {}, {}, {}
    try:
        depth = inspect_depth(drow,cutoff)
    except (ValueError, KeyError, TypeError) as exc:
        errors['depth'] = str(exc)
    try:
        quotes, counts = inspect_bbo(qrow,cutoff)
    except (ValueError, KeyError, TypeError) as exc:
        errors['bbo'] = str(exc)
    result = []
    for pair,symbol in zip(drow['pairs'],drow['symbols'],strict=True):
        state = depth.get(symbol)
        observation = state['observation'] if state else None
        stamp = state['last_update'] if state else None
        current = quotes.get(symbol,{})
        latest = max((q for q in current.values() if q is not None),
                     key=lambda q:q['received_ns'],default=None)
        matching = current.get(stamp['sequence']) if stamp else None
        matches = bool(matching and observation and all(
            Decimal(matching[side])==Decimal(observation[side]) for side in ('bid','ask')))
        fresh = bool(stamp and 0 <= cutoff-stamp['event_ns'] <= freshness
                     and 0 <= cutoff-stamp['received_ns'] <= freshness)
        both = bool(observation and fresh and observation['bid_band_complete']
                    and observation['ask_band_complete'])
        quote_fresh = bool(latest and 0<=cutoff-latest['received_ns']<=freshness)
        result.append(dict(
            pair=pair, depth_capture=drow['id'], bbo_capture=qrow['id'], cutoff_ns=cutoff,
            depth=state, ticker_counts=dict(counts.get(symbol,{})), latest_ticker=latest,
            latest_ticker_receive_fresh=quote_fresh, depth_clocks_fresh=fresh,
            bid_ask_within_snapshot=bool(observation and fresh and observation['bbo_within_known_snapshot_region']),
            both_sides_20bps_complete=both, same_id_ticker=matching,
            same_id_prices_match=matches, capture_errors=errors))
    return result


def run():
    audit = json.loads(PLAN.read_text())
    verify(audit)
    plan = json.loads(CAPTURE_PLAN.read_text())
    manifest = json.loads(CAPTURE_RESULT.read_text())
    if manifest['registration_sha256'] != sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration mismatch')
    groups = defaultdict(dict)
    for row in manifest['receipts']:
        groups[row['group']][row['kind']] = row
    result = defaultdict(list)
    for group in groups.values():
        row = group['depth']
        key = row['market']+'_'+row['window']
        cutoff = nanoseconds(row['day']+'T00:00:00Z')+plan['cutoff_minute']*MINUTE
        result[key].extend(summarize(group,cutoff,plan['liquidity_freshness_seconds']*SECOND))
    checks = {}
    for key,rows in result.items():
        checks[key] = dict(frozen_pairs=len(rows),
            captured_rest_snapshot=sum(bool(r['depth'] and r['depth']['counts'].get('snapshots',0)) for r in rows),
            replayed_at_cutoff=sum(bool(r['depth'] and r['depth']['observation']) for r in rows),
            fresh_known_bbo=sum(r['bid_ask_within_snapshot'] for r in rows),
            fresh_full_both_sides_20bps=sum(r['both_sides_20bps_complete'] for r in rows),
            fresh_ticker_receive=sum(r['latest_ticker_receive_fresh'] for r in rows),
            same_id_price_matches=sum(r['same_id_prices_match'] for r in rows),
            symbols_with_capture_errors=sum(bool(r['capture_errors']) for r in rows))
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    verify(audit)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()), checks=checks, pair_points=dict(result),
                      frozen_financial_inputs_verified=len(frozen['input_hashes']),
                      historical_depth_admission=False, financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
