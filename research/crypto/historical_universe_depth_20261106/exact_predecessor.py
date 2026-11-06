"""Resolve only failed bootstrap cases whose raw pu equals the received snapshot ID."""
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

from audit_universe import HERE, ROOT, FROZEN, SECOND, exchange_ns, inspect_bbo, timeline
from audit_universe import CAPTURE_RESULT, PLAN as FIRST_PLAN, RESULT as FIRST_RESULT
from capture_universe import now, sha, verify, write
from book_replay import Book

PLAN = HERE/'predecessor_registration.json'
RESULT = HERE/'predecessor_result.json'
GAP = re.compile(r'sequence gap: previous=(\d+), U=(\d+), u=(\d+), pu=(\d+)')


def register():
    first_plan = json.loads(FIRST_PLAN.read_text())
    verify(first_plan)
    first = json.loads(FIRST_RESULT.read_text())
    if first['registration_sha256'] != sha(FIRST_PLAN.read_bytes()):
        raise ValueError('Initial replay registration mismatch')
    targets = []
    for cohort,rows in first['pair_points'].items():
        if not cohort.startswith('futures_'):
            continue
        for row in rows:
            state = row['depth']
            if state is None or state['observation'] is not None or len(state['errors']) != 1:
                continue
            detail = state['errors'][0]
            match = GAP.fullmatch(detail['reason'])
            if not match:
                continue
            last, lo, hi, prev = map(int, match.groups())
            if last == prev < lo <= hi:
                targets.append(dict(cohort=cohort, pair=row['pair'], depth_capture=row['depth_capture'],
                                    bbo_capture=row['bbo_capture'], cutoff_ns=row['cutoff_ns'],
                                    expected_snapshot_id=last, expected_first_update_id=hi,
                                    expected_snapshot_line=detail['source_line']))
    if len(targets) != 16:
        raise ValueError('Unexpected exact predecessor recovery set')
    paths = (Path(__file__), FIRST_PLAN, FIRST_RESULT, CAPTURE_RESULT)
    write(PLAN,dict(registered_at_utc=now(), targets=targets,
                    input_hashes={**first_plan['input_hashes'],
                                  **{str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in paths}},
                    policy='Retest only original bootstrap failures with raw pu equal to the captured '
                           'snapshot ID. Do not skip a missing sequence or use a future snapshot.',
                    financial_accounts=0))


def recover(target, depth, ticker):
    symbol = target['pair'].split(':')[0].replace('/','').lower()
    cutoff = target['cutoff_ns']
    book = Book('futures')
    pending = []
    snapshot, first, last = None, None, None
    applied = 0
    for line, receive, parsed in timeline(depth):
        if receive > cutoff:
            continue
        if parsed is None:
            raise ValueError('A disconnect is inside or could straddle the cutoff')
        stream_symbol, channel, message = parsed
        if stream_symbol != symbol:
            continue
        data = message['data']
        if channel == 'depthSnapshot':
            if (snapshot is not None or not message.get('generated') is True
                    or data['lastUpdateId'] != target['expected_snapshot_id']
                    or line != target['expected_snapshot_line']):
                raise ValueError('Original received snapshot identity does not match')
            event = exchange_ns(data,depth,receive,required=True)
            book.snapshot(data)
            snapshot = dict(sequence=book.last, source_line=line, received_ns=receive,
                            event_ns=event, bid_boundary=str(book.bid_boundary),
                            ask_boundary=str(book.ask_boundary))
            updates, pending = pending, []
        else:
            if not channel.startswith('depth@') or data.get('s','').lower()!=symbol:
                raise ValueError('Unexpected native depth update')
            event = exchange_ns(data,depth,receive,required=True)
            stamp = dict(sequence=data['u'], previous=data['pu'], first=data['U'],
                         source_line=line, received_ns=receive, event_ns=event)
            if snapshot is None:
                pending.append((data,stamp))
                continue
            updates = [(data,stamp)]
        for update, stamp in updates:
            if first is None:
                if update['u'] <= book.last:
                    continue
                if (update['pu'] != book.last or not book.last < update['U'] <= update['u']
                        or update['u'] != target['expected_first_update_id']):
                    raise ValueError('First advancing update does not directly follow the original snapshot')
                first = stamp
                book.ready = True  # Book.update now enforces exact pu for this update and later updates.
            if book.update(update):
                applied += 1
                last = stamp
    if snapshot is None or first is None or last is None:
        raise ValueError('No complete observed exact predecessor chain')
    obs = book.observation()
    if obs is None:
        raise ValueError('Anchored observation is unavailable')
    effective_receive = max(snapshot['received_ns'],last['received_ns'])
    fresh = all(0 <= cutoff-t <= 15*SECOND for t in (last['event_ns'],effective_receive))
    quotes, _ = inspect_bbo(ticker,cutoff)
    quote = quotes[symbol].get(book.last)
    prices_match = bool(quote and all(Decimal(quote[key])==Decimal(obs[key]) for key in ('bid','ask')))
    return dict(snapshot=snapshot, first_bridge_update=first, last_update=last, applied_updates=applied,
                known_at_ns=effective_receive, clocks_fresh=fresh, observation=obs, same_id_ticker=quote,
                same_id_prices_match=prices_match,
                fresh_known_bbo=fresh and obs['bbo_within_known_snapshot_region'],
                both_sides_20bps_complete=fresh and obs['bid_band_complete'] and obs['ask_band_complete'])


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    cases = {row['id']:row for row in json.loads(CAPTURE_RESULT.read_text())['receipts']}
    rows = []
    for target in plan['targets']:
        try:
            outcome = recover(target,cases[target['depth_capture']],cases[target['bbo_capture']])
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            outcome = dict(error=str(exc))
        rows.append(dict(target,**outcome))
    verify(json.loads(FROZEN.read_text()))
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()), rows=rows,
                     verified_direct_predecessor_chains=sum('error' not in row for row in rows),
                     new_fresh_full_both_sides_20bps=sum(row.get('both_sides_20bps_complete',False) for row in rows),
                     historical_depth_admission=False,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
