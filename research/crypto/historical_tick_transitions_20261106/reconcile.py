"""Reconcile decoded collector objects and diagnose the DYDX trade archive conflict."""
from collections import Counter, defaultdict
import csv
from decimal import Decimal
from io import TextIOWrapper
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pandas as pd

from audit_transitions import HERE, ROOT, FROZEN, PLAN as FIRST_PLAN, RESULT as FIRST_RESULT
from audit_transitions import CAPTURE_PLAN, CAPTURE_RESULT, collector, examine, official_rows
from audit_transitions import parse_ns, receipt_body
from capture_transitions import now, sha, verify, write
from download_zst import PLAN as ZSTD_PLAN, RESULT as ZSTD_RESULT
from followup import PLAN as EXTRA_PLAN, RESULT as EXTRA_RESULT

PLAN = HERE/'reconciliation_registration.json'
RESULT = HERE/'verification.json'
DYDX = 'DYDX/USDT:USDT'
FIVE_MIN_MS = 300_000


def load(registration, result):
    plan = json.loads(registration.read_text())
    verify(plan)
    value = json.loads(result.read_text())
    if value['registration_sha256']!=sha(registration.read_bytes()):
        raise ValueError('Reconciliation registration mismatch')
    return plan,value


def register():
    first, _ = load(FIRST_PLAN,FIRST_RESULT)
    _, extra = load(EXTRA_PLAN,EXTRA_RESULT)
    _, compressed = load(ZSTD_PLAN,ZSTD_RESULT)
    run = json.loads(FROZEN.read_text())['runs'][0]
    candle_rel = 'futures/DYDX_USDT_USDT-5m-futures.feather'
    candle = ROOT/run['datadir']/candle_rel
    receipts = ROOT/run['data_receipt']
    if sha(candle.read_bytes())!=json.loads(receipts.read_text())[candle_rel]:
        raise ValueError('Frozen DYDX candle source changed')
    paths = [Path(__file__),FROZEN,FIRST_RESULT,EXTRA_RESULT,ZSTD_RESULT,receipts,candle,
             HERE/'reconciliation_attempt_v1.json']
    for row in extra['receipts']:
        if row['source']!='redirect_path_only':
            paths.append(receipt_body(row))
    for row in compressed['receipts']:
        paths.extend((receipt_body(row),receipt_body(row['decoded']|{'id':row['id']})))
    write(PLAN,dict(registered_at_utc=now(),frozen_dydx_5m=str(candle.relative_to(ROOT)),
                    input_hashes={**first['input_hashes'],**{
                        str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in paths}},
                    scope='Exact official trade ID and price comparison, use aggregate singletons only. '
                          'Never rescale or replace an original archive or model input.',
                    financial_accounts=0))


def read_csv(archive_row, checksum_row):
    archive_path = receipt_body(archive_row)
    checksum = receipt_body(checksum_row).read_text().split()
    if checksum!=[archive_row['sha256'],archive_row['url'].rsplit('/',1)[1]]:
        raise ValueError('New official archive checksum mismatch')
    with ZipFile(archive_path) as archive:
        member, = archive.infolist()
        if member.file_size>64*1024**2:
            raise ValueError('Unexpected DYDX archive size')
        with archive.open(member) as handle:
            yield from csv.DictReader(TextIOWrapper(handle,encoding='utf-8'))


def by_source(receipts,kind):
    return [next(row for row in receipts if row.get('kind')==kind and row['source']==source)
            for source in ('official_zip','official_checksum')]


def check_dydx(transition, matched, extra, plan, first_plan):
    trade_archive = next(row for row in matched if row['source']=='official_zip')
    trade_checksum = next(row for row in matched if row['source']=='official_checksum')
    hours = sorted([row for row in matched if row['source']=='collector'],key=lambda row:row['hour'])
    deadline = parse_ns(transition['deadline'])//10**6
    end = parse_ns(transition['suspension_end'])//10**6
    start_hour, last_hour = hours[0]['hour'], hours[-1]['hour']
    start = parse_ns(transition['day']+f'T{start_hour:02d}:00:00Z')//10**6
    stop = parse_ns(transition['day']+f'T{last_hour+1:02d}:00:00Z')//10**6

    raw, received = {}, {}
    for row in hours:
        records,_ = collector(row)
        if received.keys() & records.keys():
            raise ValueError('Collector overlap across hours')
        received.update(records)
        for trade in official_rows(dict(trade_archive,hour=row['hour']),trade_checksum,first_plan,Counter()):
            raw[trade[0]] = trade

    counts, examples, within = defaultdict(Counter), [], []
    for row in read_csv(*by_source(extra,'aggTrades')):
        first_id, last_id = int(row['first_trade_id']),int(row['last_trade_id'])
        time = int(row['transact_time'])
        if not start<=time<stop or first_id!=last_id or first_id not in raw:
            continue
        _, at, price, qty, maker = raw[first_id]
        if (time,Decimal(row['quantity']),row['is_buyer_maker']=='true')!=(at//10**6,qty,maker):
            continue
        aggregate_price = Decimal(row['price'])
        segment = 'before' if time<deadline else ('scheduled_suspension' if time<end else 'after')
        counts[segment]['exact_id_time_quantity_maker'] += 1
        if price==aggregate_price:
            relationship = 'same_price'
        elif price*1000==aggregate_price:
            relationship = 'aggregate_is_1000x_raw_price'
        else:
            relationship = 'other_price_disagreement'
        counts[segment][relationship] += 1
        record = dict(trade_id=first_id,at_ms=time,raw_price=str(price),aggregate_price=str(aggregate_price),
                      quantity=str(qty),relationship=relationship)
        if first_id in received:
            p,q,t,m,event,receive = received[first_id]
            if (q,t,m)==(qty,time,maker) and t<=event<=receive//10**6:
                record.update(collector_price=str(p),collector_receive_ns=receive,
                              collector_matches_aggregate_price=p==aggregate_price,
                              collector_matches_raw_price=p==price)
                counts[segment]['collector_matches_aggregate_price'] += int(p==aggregate_price)
                counts[segment]['collector_matches_raw_price'] += int(p==price)
        if segment=='scheduled_suspension':
            within.append(record)
        elif len(examples)<5 and relationship!='same_price':
            examples.append(record)

    candles = pd.read_feather(ROOT/plan['frozen_dydx_5m'])
    frozen = {int(row.date.value//10**6):row for row in candles.itertuples() if start<=row.date.value//10**6<stop}
    fields = ('open','high','low','close','volume')
    kline_matches, kline_rows = 0,0
    for row in read_csv(*by_source(extra,'klines')):
        open_time = int(row['open_time'])
        if not start<=open_time<stop:
            continue
        if open_time not in frozen or int(row['close_time'])!=open_time+FIVE_MIN_MS-1:
            raise ValueError('Unexpected DYDX candle identity')
        kline_rows += 1
        kline_matches += all(Decimal(row[k])==Decimal(str(getattr(frozen[open_time],k))) for k in fields)
    return dict(segments={key:dict(value) for key,value in counts.items()},examples=examples,
                announced_suspension_singleton_trade_witnesses=within,official_klines_in_scope=kline_rows,
                frozen_native_ohlcv_exact_matches=kline_matches,original_trade_archive_rewritten=False)


def audit():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    capture, original = load(CAPTURE_PLAN,CAPTURE_RESULT)
    first_plan, first = load(FIRST_PLAN,FIRST_RESULT)
    _, extra = load(EXTRA_PLAN,EXTRA_RESULT)
    _, compressed = load(ZSTD_PLAN,ZSTD_RESULT)
    replacement = {row['id'].removesuffix('-zstd'):row for row in compressed['receipts']}
    receipts = defaultdict(list)
    for row in original['receipts']:
        if row['id'] in replacement:
            item = replacement[row['id']]
            receipt_body(item)
            receipt_body(item['decoded']|{'id':item['id']})
            row = dict(item,**item['decoded'])
        receipts[row['group']].append(row)
    rebuilt = []
    for transition, earlier in zip(capture['transitions'],first['rows'],strict=True):
        outcome = examine(transition,receipts[transition['key']],first_plan)
        if outcome['segments']!=earlier['segments']:
            raise ValueError('Corrected collector ingestion changed official trade grid result')
        rebuilt.append(outcome)
    transition = next(t for t in capture['transitions'] if t['pair']==DYDX)
    dydx = check_dydx(transition,receipts[transition['key']],extra['receipts'],plan,first_plan)
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    return dict(registration_sha256=sha(PLAN.read_bytes()),rows=rebuilt,
                newly_verified_spot_notice_changes=8,
                official_transition_trade_windows=len(rebuilt),
                collector_compressed_objects_recovered=len(replacement),
                trade_grid_source_conflict=dydx,
                frozen_financial_inputs_verified=len(frozen['input_hashes']),
                continuous_historical_rule_admission=False,financial_accounts=0)


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        write(RESULT,audit())
    else:
        raise SystemExit('Use register or run')
