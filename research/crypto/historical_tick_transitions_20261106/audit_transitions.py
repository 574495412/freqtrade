"""Verify exact new-grid public trade witnesses around announced rule transitions."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys

from capture_transitions import HERE, ROOT, FROZEN, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture import now, sha, verify, write

TRADES = HERE.parent/'historical_trade_clock_20261106'
sys.path.insert(0,str(TRADES))
from audit_trades import NS_PER_MS, collector, official_rows, receipt_body

PLAN = HERE/'audit_registration.json'
RESULT = HERE/'audit_result.json'
HOUR_NS = 3600*10**9


def parse_ns(value):
    return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())*10**9


def register():
    capture = json.loads(CAPTURE_PLAN.read_text())
    verify(capture)
    result = json.loads(CAPTURE_RESULT.read_text())
    if result['registration_sha256']!=sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration mismatch')
    inputs = [Path(__file__),TRADES/'audit_trades.py',CAPTURE_PLAN,CAPTURE_RESULT]
    for row in result['receipts']:
        if 'path' in row:
            inputs.append(receipt_body(row))
    write(PLAN,dict(registered_at_utc=now(),
                    input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in inputs},
                    official_archive_uncompressed_cap_bytes=512*1024**2,
                    scope='Two UTC hours: preceding and containing the announced boundary; '
                          'same-ID collector observation for exact official witnesses. '
                          'No inferred first effective instant or blanket permission for old orders.',
                    financial_accounts=0))


def available(row):
    return (row.get('http_status'),row.get('state'))==(200,'body_complete')


def clocked_witness(item, observed):
    if item is None:
        return None
    trade, at, price, qty, maker = item
    result = dict(trade_id=trade,exchange_trade_ns=at,price=str(price),quantity=str(qty),
                  is_buyer_maker=maker,collector_exact_match=False)
    if trade not in observed:
        return result
    p,q,t,m,event,received = observed[trade]
    match = ((p,q,t,m)==(price,qty,at//NS_PER_MS,maker)
             and t<=event<=received//NS_PER_MS and at<=received)
    return dict(result,collector_exact_match=match,collector_trade_ms=t,
                collector_exchange_event_ms=event,collector_received_ns=received)


def examine(transition, receipts, plan):
    by_source = defaultdict(list)
    for row in receipts:
        by_source[row['source']].append(row)
    archive, = by_source['official_zip']
    checksum, = by_source['official_checksum']
    hours = sorted(by_source['collector'],key=lambda row:row['hour'])
    if not available(archive) or not available(checksum):
        return dict(transition,status='official_archive_unavailable',archive_http_status=archive.get('http_status'),
                    checksum_http_status=checksum.get('http_status'))
    deadline = parse_ns(transition['deadline'])
    suspension_end = parse_ns(transition['suspension_end']) if transition['suspension_end'] else deadline
    old,new = Decimal(transition['old_tick']),Decimal(transition['new_tick'])
    if not 0<new<old or old%new:
        raise ValueError('New grid is not a strict refinement')
    stats = defaultdict(Counter)
    witness = {'first_finer_before_deadline':None,'first_finer_after_deadline':None,
               'last_before_deadline':None,'first_after_suspension_or_deadline':None}
    observed, collector_errors = {}, []
    for row in hours:
        if not available(row):
            collector_errors.append(dict(id=row['id'],http_status=row.get('http_status'),state=row.get('state')))
            continue
        records, _ = collector(row)
        if observed.keys() & records.keys():
            raise ValueError('Collector trade IDs repeated across the two hours')
        observed.update(records)
    for row in hours:
        ignored_stats = Counter()
        for item in official_rows(dict(archive,hour=row['hour']),checksum,plan,ignored_stats):
            _,at,price,_,_ = item
            if at<deadline:
                segment = 'before_deadline'
                witness['last_before_deadline'] = item
            elif at<suspension_end:
                segment = 'announced_suspension'
            else:
                segment = 'after_suspension_or_deadline'
                if witness['first_after_suspension_or_deadline'] is None:
                    witness['first_after_suspension_or_deadline'] = item
            stats[segment]['trades'] += 1
            if price%new:
                stats[segment]['off_both_grids'] += 1
            elif price%old:
                stats[segment]['new_grid_only'] += 1
                key = 'first_finer_before_deadline' if at<deadline else 'first_finer_after_deadline'
                if witness[key] is None:
                    witness[key] = item
            else:
                stats[segment]['old_grid_compatible'] += 1
    return dict(transition,status='verified_official_public_trades',
                registered_start_ns=deadline//HOUR_NS*HOUR_NS-HOUR_NS,
                registered_end_ns=deadline//HOUR_NS*HOUR_NS+HOUR_NS,
                segments={key:dict(stats[key]) for key in ('before_deadline','announced_suspension',
                                                            'after_suspension_or_deadline')},
                witnesses={key:clocked_witness(item,observed) for key,item in witness.items()},
                collector_unavailable=collector_errors)


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    captured = json.loads(CAPTURE_PLAN.read_text())
    verify(captured)
    receipts = defaultdict(list)
    for row in json.loads(CAPTURE_RESULT.read_text())['receipts']:
        receipts[row['group']].append(row)
    rows = [examine(t,receipts[t['key']],plan) for t in captured['transitions']]
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),rows=rows,
                      frozen_financial_inputs_verified=len(frozen['input_hashes']),
                      continuous_historical_rule_admission=False,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
