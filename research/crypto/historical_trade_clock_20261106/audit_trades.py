"""Audit archived trade IDs, collector clocks and frozen candle reconstruction."""
from collections import Counter, defaultdict
import csv
from decimal import Decimal
from io import TextIOWrapper
from itertools import chain
import json
from math import isclose
from pathlib import Path
import sys
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.feather as feather
import pyarrow.parquet as pq

from capture_trades import HERE, PRIOR, ROOT, FUTURES_L
from capture_trades import PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from audit import NS_PER_MS, NS_PER_SECOND, nanoseconds, receipt_body
from capture import DATA, now, sha, verify, write
from probe_event_sources import decode

PLAN = HERE / 'audit_registration.json'
RESULT = HERE / 'audit_result.json'
SOURCE_PROBE = DATA / 'event_source_audit.json'
HOUR = 3600 * NS_PER_SECOND
FIVE_MINUTES = 300 * NS_PER_SECOND
COLLECTOR_COLUMNS = ('received_time', 'event_time', 'symbol', 'trade_id', 'price',
                     'quantity', 'trade_time', 'is_buyer_maker', 'order_type')
OHLCV = ('open', 'high', 'low', 'close', 'volume')
FUTURES_HEADER = ['id', 'price', 'qty', 'quote_qty', 'time', 'is_buyer_maker']


def register():
    capture = json.loads(CAPTURE_PLAN.read_text())
    verify(capture)
    manifest = json.loads(CAPTURE_RESULT.read_text())
    if manifest['registration_sha256'] != sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration hash mismatch')
    rows = manifest['receipts']
    if len(rows) != 12 or any((row.get('http_status'), row.get('state')) != (200, 'body_complete')
                             for row in rows):
        raise ValueError('Incomplete public trade downloads')
    inputs = (Path(__file__), CAPTURE_PLAN, CAPTURE_RESULT, PRIOR / 'audit.py',
              FUTURES_L, SOURCE_PROBE, *(receipt_body(row) for row in rows))
    hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs}
    write(PLAN, dict(registered_at_utc=now(), pyarrow_version=pa.__version__,
                     source_timezone='UTC', minimum_registered_files=12,
                     official_archive_uncompressed_cap_bytes=512 * 1024**2,
                     spot_time_unit='microseconds', futures_time_unit='milliseconds',
                     collector_event_trade_time_unit='milliseconds', collector_receive_unit='nanoseconds',
                     identity_policy='Same market, symbol, trade ID, price, base quantity, maker side and '
                                     'exchange trade time. For spot compare matching millisecond buckets '
                                     'while retaining official microseconds. Duplicate collector IDs are excluded.',
                     volume_relative_tolerance=1e-12, volume_absolute_tolerance=1e-10,
                     timing_policy='Latest matched constituent receipt is a hindsight bound. No '
                                   'watermark, native final-kline publication or strategy receive is imputed.',
                     input_hashes={**capture['input_hashes'], **hashes}, financial_accounts=0))


def start_ns(row):
    return nanoseconds(f"{row['day']}T{row['hour']:02d}:00:00Z")


def collector(row):
    file = pq.ParquetFile(receipt_body(row))
    if tuple(file.schema_arrow.names) != COLLECTOR_COLUMNS:
        raise ValueError('Unknown collector trade schema')
    start = start_ns(row)
    trades, duplicates, stats = {}, set(), Counter()
    previous_received, previous_id = start, None
    for batch in file.iter_batches(batch_size=65536):
        data = batch.to_pydict()
        for received, event, symbol, tid, price, qty, traded, maker, order_type in zip(
                *(data[key] for key in COLLECTOR_COLUMNS), strict=True):
            stats['rows'] += 1
            if symbol != row['symbol'] or not start <= received < start + HOUR:
                raise ValueError('Collector identity or hour outside registration')
            if received < previous_received:
                stats['receipt_clock_regressions'] += 1
            if previous_id is not None:
                if tid <= previous_id:
                    stats['nonadvancing_id_rows'] += 1
                elif tid > previous_id + 1:
                    stats['forward_id_gap_events'] += 1
                    stats['skipped_ids_across_forward_gaps'] += tid - previous_id - 1
            previous_received, previous_id = received, tid
            p, q = Decimal(price), Decimal(qty)
            if not p.is_finite() or not q.is_finite() or p < 0 or q < 0:
                raise ValueError('Invalid collector trade economics')
            if order_type == 'NA' and (p, q) == (0, 0):
                stats['nontrade_markers'] += 1
                continue
            if p <= 0 or q <= 0:
                stats['invalid_economic_rows'] += 1
                continue
            if tid in trades:
                duplicates.add(tid)
            if not traded <= event <= received // NS_PER_MS:
                stats['collector_clock_conflicts'] += 1
            trades[tid] = (p, q, traded, maker, event, received)
    for tid in duplicates:
        trades.pop(tid)
    stats['duplicated_ids_excluded'] = len(duplicates)
    if stats['rows'] != file.metadata.num_rows:
        raise ValueError('Parquet row count mismatch')
    return trades, stats


def official_rows(zip_row, check_row, plan, stats):
    checksum = receipt_body(check_row).read_text().split()
    expected_name = zip_row['url'].rsplit('/', 1)[1]
    if checksum != [zip_row['sha256'], expected_name]:
        raise ValueError('Official SHA-256 checksum differs')
    path = receipt_body(zip_row)
    start = start_ns(zip_row)
    futures = zip_row['market'] == 'futures'
    scale = NS_PER_MS if futures else 1000
    previous_id, previous_time = None, start
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != 1 or entries[0].filename != expected_name.removesuffix('.zip') + '.csv'
                or entries[0].file_size > plan['official_archive_uncompressed_cap_bytes']):
            raise ValueError('Unexpected official archive member or size')
        with archive.open(entries[0]) as member:
            raw = csv.reader(TextIOWrapper(member, encoding='utf-8'))
            if futures:
                if next(raw) != FUTURES_HEADER:
                    raise ValueError('Official futures header changed')
            else:
                first = next(raw)
                raw = chain((first,), raw)
            for fields in raw:
                if len(fields) != (6 if futures else 7):
                    raise ValueError('Official trade column count changed')
                at = int(fields[4]) * scale
                if at >= start + HOUR:
                    break
                if at < start:
                    continue
                tid = int(fields[0])
                if previous_id is not None and tid <= previous_id:
                    raise ValueError('Official trade IDs did not advance')
                if previous_id is not None and tid > previous_id + 1:
                    stats['official_skipped_ids'] += tid - previous_id - 1
                if at < previous_time:
                    stats['official_trade_time_regressions'] += 1
                previous_id, previous_time = tid, at
                price, quantity = Decimal(fields[1]), Decimal(fields[2])
                maker = fields[5].lower()
                if maker not in ('true', 'false') or not price.is_finite() or not quantity.is_finite():
                    raise ValueError('Invalid official trade field')
                if price == 0 and quantity == 0:
                    stats['official_nontrade_markers'] += 1
                    continue
                if price <= 0 or quantity <= 0:
                    raise ValueError('Nonpositive official trade')
                stats['official_economic_rows'] += 1
                yield tid, at, price, quantity, maker == 'true'


def frozen_candles(path, start):
    table = feather.read_table(ROOT / path, columns=['date', *OHLCV])
    timestamps = pc.cast(pc.cast(table['date'], pa.timestamp('ns', tz='UTC')), pa.int64())
    mask = pc.and_(pc.greater_equal(timestamps, start), pc.less(timestamps, start + HOUR))
    subset = table.filter(mask)
    data = subset.to_pydict()
    times = pc.filter(timestamps, mask).to_pylist()
    if times != [start + n * FIVE_MINUTES for n in range(12)]:
        raise ValueError('Missing or duplicate frozen 5m candle')
    return list(zip(*(data[key] for key in OHLCV), strict=True))


def summary(values):
    if not values:
        return None
    values.sort()
    return {name: values[(len(values) - 1) * percentile // 100]
            for name, percentile in [('min_ns', 0), ('p50_ns', 50), ('p95_ns', 95), ('max_ns', 100)]}


def join(rows, candle_path, plan):
    hour, official, checksum = (rows[key] for key in ('collector', 'official_zip', 'official_checksum'))
    start = start_ns(hour)
    trades, collector_stats = collector(hour)
    candles = frozen_candles(candle_path, start)
    stats = Counter()
    bars = [[] for _ in range(12)]
    event_lags, trade_lags = [], []
    discrepancies = []
    for tid, at, price, quantity, maker in official_rows(official, checksum, plan, stats):
        observed = trades.pop(tid, None)
        matched_received = None
        if observed is None:
            stats['missing_collector_id'] += 1
        else:
            p, q, traded, side, event, received = observed
            if (p, q, traded, side) != (price, quantity, at // NS_PER_MS, maker):
                stats['same_id_field_conflicts'] += 1
                if len(discrepancies) < 6:
                    discrepancies.append(dict(trade_id=tid, official_trade_ns=at,
                        collector_trade_ms=traded, price_match=p == price, qty_match=q == quantity,
                        maker_match=side == maker))
            else:
                stats['same_id_economic_and_clock_bucket_matches'] += 1
                if traded <= event <= received // NS_PER_MS and at <= received:
                    matched_received = received
                    event_lags.append(received - event * NS_PER_MS)
                    trade_lags.append(received - at)
                else:
                    stats['same_id_causal_clock_conflicts'] += 1
        bars[(at-start)//FIVE_MINUTES].append((tid, at, price, quantity, matched_received))

    outside, extras = 0, 0
    for _, _, traded, _, _, _ in trades.values():
        if start <= traded * NS_PER_MS < start + HOUR:
            extras += 1
        else:
            outside += 1
    stats['collector_only_in_hour'] = extras
    stats['collector_receipts_for_trades_outside_hour'] = outside
    details = []
    for i, (items, frozen) in enumerate(zip(bars, candles, strict=True)):
        if not items:
            raise ValueError('An official bar contains no trades')
        prices = [item[2] for item in items]
        quantity = sum((item[3] for item in items), Decimal(0))
        ohlcv = [prices[0], max(prices), min(prices), prices[-1], quantity]
        observed = [item[4] for item in items if item[4] is not None]
        end = start + (i + 1) * FIVE_MINUTES
        complete = len(observed) == len(items)
        detail = dict(
            start_ns=end-FIVE_MINUTES, end_ns=end, official_trades=len(items),
            verified_collector_receipts=len(observed), verified_receipts_at_or_after_end=sum(ns>=end for ns in observed),
            complete_observed_constituents=complete, official_ohlcv=dict(zip(OHLCV,map(str,ohlcv),strict=True)),
            frozen_ohlcv=dict(zip(OHLCV,frozen,strict=True)),
            price_fields_match=all(Decimal(str(value)) == actual for value, actual in zip(frozen[:4],ohlcv[:4],strict=True)),
            volume_matches=isclose(frozen[4], float(quantity), rel_tol=plan['volume_relative_tolerance'],
                                   abs_tol=plan['volume_absolute_tolerance']),
            last_trade_id=items[-1][0], last_trade_at_ns=items[-1][1],
            last_trade_observed_at_ns=items[-1][4],
            latest_matched_constituent_receipt_ns=max(observed,default=None),
            all_constituents_receipt_minus_end_ns=max(observed)-end if complete else None,
            last_bar_at_file_edge=i==11,
        )
        details.append(detail)
    return dict(id=hour['group'], official_checksum_verified=True, collector_counts=dict(collector_stats),
                join_counts=dict(stats), receipt_minus_exchange_event=summary(event_lags),
                receipt_minus_official_trade=summary(trade_lags), discrepancy_examples=discrepancies,
                bars=details)


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if RESULT.exists() or pa.__version__ != plan['pyarrow_version']:
        raise ValueError('Audit already performed or environment changed')
    groups = defaultdict(dict)
    for row in json.loads(CAPTURE_RESULT.read_text())['receipts']:
        groups[row['group']][row['source']] = row
    sources = json.loads(CAPTURE_PLAN.read_text())['frozen_candles']
    results = []
    for key, rows in groups.items():
        result = join(rows, sources[key], plan)
        results.append(result)
        print(key, result['join_counts'], flush=True)
    frozen = json.loads(FUTURES_L.read_text())
    verify(frozen)
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), results=results,
                       frozen_financial_inputs_verified=len(frozen['input_hashes']),
                       exchange_kline_publication_verified=False, strategy_received_time_verified=False,
                       account_fill_evidence=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
