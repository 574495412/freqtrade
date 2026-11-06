"""Recover public settlement clocks without assigning private account event order."""
from collections import Counter, defaultdict
import csv
from decimal import Decimal
from io import TextIOWrapper
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pandas as pd

from capture import HERE, ROOT, LONG, SHORT, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture import DAY, bounds, now, sha, verify, write
from book_replay import nanoseconds
from probe_event_sources import decode

PLAN = HERE/'audit_registration.json'
RESULT = HERE/'audit_result.json'
HOUR = 3_600_000
LONG_SOURCE = ROOT/'user_data/crypto_quant/nfi40_data_v002/sources.json'
SHORT_SOURCE = ROOT/'user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001/sources.json'
ARCHIVE_HEADER = ['calc_time','funding_interval_hours','last_funding_rate']
ACCOUNT_ROOT = ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'


def get(path):
    return json.loads(path.read_text())


def checked(path, expected):
    body = path.read_bytes()
    if sha(body) != expected:
        raise ValueError('Source hash differs: '+str(path))
    return body


def raw_receipt(row):
    if (row.get('http_status'),row.get('response_state')) != (200,'body_complete'):
        raise ValueError('Capture unavailable: '+row['id'])
    return decode(checked(ROOT/row['path'],row['sha256']),row['headers'])


def register():
    capture = get(CAPTURE_PLAN)
    verify(capture)
    acquired = get(CAPTURE_RESULT)
    if acquired['registration_sha256'] != sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration mismatch')
    inputs = [Path(__file__),CAPTURE_PLAN,CAPTURE_RESULT,LONG_SOURCE,SHORT_SOURCE]
    for source in (LONG,SHORT):
        plan = get(source)
        inputs.append(ROOT/plan['runs'][0]['data_receipt'])
        for run in plan['runs']:
            directory = ACCOUNT_ROOT/run['id']
            archives = list((directory/'native_results').glob('*.zip'))
            if len(archives)!=1:
                raise ValueError('Ambiguous sealed native export')
            manifest_path = directory/'artifact_hashes.json'
            hashes = get(manifest_path)
            archive_path = archives[0]
            checked(archive_path,hashes[str(archive_path.relative_to(directory))])
            inputs.extend((manifest_path,archive_path))
    write(PLAN,dict(registered_at_utc=now(),
                    input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in inputs},
                    scope='All 40 frozen contracts and both windows, original official events vs native hour; '
                          'public sampled collector poll identity; unchanged modeled fill clock comparison.',
                    financial_accounts=0))


def funding_sources():
    long = defaultdict(list)
    short = defaultdict(list)
    for row in get(LONG_SOURCE):
        if row['kind']=='funding' and '2025-01'<=row['month']<='2026-08':
            long[row['symbol']].append(row)
    for row in get(SHORT_SOURCE):
        if row['kind']=='funding_api' or (row['kind']=='funding' and row['month']=='2026-10'):
            short[row['symbol']].append(row)
    return {'L':long,'S':short}


def add_event(events, symbol, time, value, source, begin, end):
    if type(time) is not int or time<=0 or not value.is_finite():
        raise ValueError('Invalid funding settlement')
    if begin<=time<end:
        if time in events:
            raise ValueError(f'Duplicate original funding clock: {symbol} {time}')
        events[time] = dict(rate=value, source=source)


def event_map(symbol, rows, begin, end):
    events = {}
    for row in rows:
        path = Path(row['path'])
        checked(path,row['sha256'])
        if row['kind']=='funding_api':
            data = get(path)
            if data['params']['symbol']!=symbol:
                raise ValueError('Archived API symbol mismatch')
            for item in data['body']:
                if item['symbol']!=symbol:
                    raise ValueError('Archived API body symbol mismatch')
                add_event(events,symbol,item['fundingTime'],Decimal(item['fundingRate']),
                          'frozen_official_api',begin,end)
        else:
            checksum = path.with_suffix('.zip.CHECKSUM').read_text().split()
            if checksum != [row['sha256'],path.name]:
                raise ValueError('Original archive checksum sidecar changed')
            with ZipFile(path) as archive:
                members = archive.infolist()
                if len(members)!=1 or members[0].file_size>2*1024**2:
                    raise ValueError('Unexpected original archive members')
                with archive.open(members[0]) as handle:
                    reader = csv.reader(TextIOWrapper(handle,encoding='utf-8'))
                    if next(reader)!=ARCHIVE_HEADER:
                        raise ValueError('Unexpected original funding schema')
                    for timestamp,interval,rate in reader:
                        if int(interval)<=0:
                            raise ValueError('Invalid archive funding interval')
                        add_event(events,symbol,int(timestamp),Decimal(rate),'frozen_monthly_archive',begin,end)
    if not events:
        raise ValueError('Original funding events absent')
    return events


def hour_map(events):
    result = {}
    for time,row in events.items():
        key = time//HOUR*HOUR
        if key in result:
            raise ValueError('Multiple original events mapped to same native hour')
        result[key] = (time,row['rate'])
    return result


def native_rates(path, receipt, begin, end):
    checked(path,receipt)
    table = pd.read_feather(path)
    if list(table)!=['date','funding_rate'] or table.date.duplicated().any():
        raise ValueError('Unexpected native funding file schema')
    return {int(at.value//10**6):Decimal(str(rate))
            for at,rate in zip(table.date,table.funding_rate,strict=True) if begin<=at.value//10**6<end}


def fresh_official(row, begin, end):
    data = json.loads(raw_receipt(row))
    if not isinstance(data,list) or not 0<len(data)<1000:
        raise ValueError('Official response empty or truncated')
    last = begin-1
    events = {}
    for item in data:
        at = item['fundingTime']
        rate = Decimal(item['fundingRate'])
        if (item['symbol']!=row['symbol'] or not last<at<end or not rate.is_finite()):
            raise ValueError('Unexpected official symbol, chronology or rate')
        events[at] = item
        last = at
    return events


def build_calendar(receipts, symbols):
    sources = funding_sources()
    native, original, official = {}, {}, {}
    short_start, short_end = bounds(SHORT)
    for row in receipts:
        if row['kind']=='official':
            official[row['symbol']] = fresh_official(row,short_start,short_end)
    if set(official)!=set(symbols):
        raise ValueError('Official short-window universe incomplete')
    stats = {}
    for window,path in [('L',LONG),('S',SHORT)]:
        start,end = bounds(path)
        run = get(path)['runs'][0]
        datadir = ROOT/run['datadir']
        hashes = get(ROOT/run['data_receipt'])
        stats[window] = {}
        for symbol in symbols:
            rows = sources[window][symbol]
            if ((window=='L' and (len(rows)!=20 or len({r['month'] for r in rows})!=20))
                    or (window=='S' and sorted(r['kind'] for r in rows)!=['funding','funding_api'])):
                raise ValueError('Historical source partition inventory mismatch')
            events = event_map(symbol,rows,start,end)
            hours = hour_map(events)
            stem = symbol.removesuffix('USDT')
            name = f'futures/{stem}_USDT_USDT-1h-funding_rate.feather'
            native_map = native_rates(datadir/name,hashes[name],start,end)
            original[window,symbol] = events
            native[window,symbol] = hours
            source_rates = {h:rate for h,(_,rate) in hours.items()}
            row = dict(
                events=len(events), native_hours=len(native_map),
                original_source_counts=dict(Counter(e['source'] for e in events.values())),
                exact_hour_events=sum(t%HOUR==0 for t in events),
                milliseconds_after_native_hour=dict(sorted(Counter(t%HOUR for t in events).items())),
                original_vs_native_missing_hours=sorted(source_rates.keys()-native_map.keys()),
                native_only_hours=sorted(native_map.keys()-source_rates.keys()),
                differing_native_rate_hours=sorted(h for h in source_rates.keys()&native_map.keys()
                                                    if source_rates[h]!=native_map[h]))
            if window=='S':
                fresh = official[symbol]
                fresh_hours = hour_map({t:dict(rate=Decimal(item['fundingRate'])) for t,item in fresh.items()})
                row.update(current_official_rows=len(fresh),
                    current_exact_time_and_rate_matches=sum(
                        t in events and events[t]['rate']==Decimal(item['fundingRate']) for t,item in fresh.items()),
                    current_hour_and_rate_matches=sum(h in hours and hours[h][1]==value
                                                     for h,(_,value) in fresh_hours.items()),
                    current_only_times=sorted(fresh.keys()-events.keys()),
                    frozen_only_times=sorted(events.keys()-fresh.keys()),
                    changed_rate_at_exact_time=sorted(t for t in fresh.keys()&events.keys()
                                                      if events[t]['rate']!=Decimal(fresh[t]['fundingRate'])))
            stats[window][symbol] = row
    return stats,native,official


def collector_evidence(receipts, official):
    start = nanoseconds(DAY+'T00:00:00Z')
    end = start+24*HOUR*10**6
    expected = {(s,t) for s,rows in official.items() for t in rows if start<=t*10**6<end}
    field_keys = ('symbol','fundingTime','fundingRate','markPrice','rateType')
    sightings = defaultdict(list)
    total = 0
    for row in receipts:
        if row['kind']!='collector':
            continue
        left = start+row['minute']*60*10**9
        previous = left
        for line_number,line in enumerate(raw_receipt(row).decode().splitlines(),1):
            if not line.strip():
                continue
            at,raw = line.split(' ',1)
            receive = nanoseconds(at)
            msg = json.loads(raw)
            data = msg['data']
            symbol,time = data['symbol'],data['fundingTime']
            if (not previous<=receive<left+60*10**9 or msg.get('generated') is not True
                    or symbol not in official or type(time) is not int or time*10**6>receive
                    or msg['stream']!=symbol.lower()+'@fundingRate'):
                raise ValueError('Collector receipt timing or identity invalid')
            previous = receive
            total += 1
            if (symbol,time) not in expected:
                continue
            ref = official[symbol][time]
            exact = all(data.get(key)==ref.get(key) for key in field_keys)
            sightings[symbol,time].append(dict(capture=row['id'],line=line_number,received_ns=receive,
                                                exact_official_fields=exact))
    records = []
    for (symbol,time),seen in sorted(sightings.items()):
        first = min(s['received_ns'] for s in seen)
        records.append(dict(symbol=symbol,settlement_ms=time,first_observed_poll_receipt_ns=first,
                            first_observed_poll_delay_ns=first-time*10**6,
                            observations=len(seen),all_official_fields_match=all(s['exact_official_fields'] for s in seen),
                            first=min(seen,key=lambda s:s['received_ns'])))
    return dict(total_source_rows=total,expected_same_day_settlements=len(expected),
                observed_same_day_settlements=len(records),missing_identities=sorted(expected-sightings.keys()),
                records=records)


def archived_fill_screen(native):
    rows = []
    for window,path in [('L',LONG),('S',SHORT)]:
        start,end = bounds(path)
        for run in get(path)['runs']:
            folder = ACCOUNT_ROOT/run['id']
            archive_path, = (folder/'native_results').glob('*.zip')
            checked(archive_path,get(folder/'artifact_hashes.json')[str(archive_path.relative_to(folder))])
            with ZipFile(archive_path) as archive:
                name, = [n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                trades = json.loads(archive.read(name))['strategy'][run['strategy']]['trades']
            counts = Counter()
            boundary = []
            for trade in trades:
                symbol = trade['pair'].split(':')[0].replace('/','')
                events = native[window,symbol]
                orders = trade['orders'][:-1] if trade['exit_reason']=='force_exit' else trade['orders']
                for order in orders:
                    when = order['order_filled_timestamp']
                    if not start<=when<end:
                        continue
                    counts['saved_model_fills'] += 1
                    nominal = when//HOUR*HOUR
                    if nominal not in events:
                        continue
                    actual,_ = events[nominal]
                    if when!=nominal and not nominal<when<=actual:
                        continue
                    relation = 'exact_same_millisecond' if when==actual else ('before_exact_event' if when<actual else 'after_exact_event')
                    counts[relation] += 1
                    boundary.append(dict(symbol=symbol,side='entry' if order['ft_is_entry'] else 'exit',
                                         saved_fill_ms=when,native_hour_ms=nominal,
                                         original_public_funding_ms=actual,relation=relation))
            rows.append(dict(run_id=run['id'],window=window,counts=dict(counts),boundary_fills=boundary))
    return rows


def run():
    plan = get(PLAN)
    verify(plan)
    capture = get(CAPTURE_PLAN)
    verify(capture)
    receipts = get(CAPTURE_RESULT)['receipts']
    stats,native,official = build_calendar(receipts,capture['symbols'])
    observed = collector_evidence(receipts,official)
    fills = archived_fill_screen(native)
    frozen = get(LONG)
    verify(frozen)
    verify(plan)
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),calendar=stats,
                      collector=observed,saved_model_fill_screen=fills,
                      frozen_financial_inputs_verified=len(frozen['input_hashes']),
                      exchange_account_settlement_and_fill_order_verified=False,
                      economic_admission=False,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
