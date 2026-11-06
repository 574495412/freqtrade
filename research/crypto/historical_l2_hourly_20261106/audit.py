"""Replay received L2 messages without upgrading provider checkpoints to full depth."""
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import groupby
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq

from download import PLAN as DOWNLOAD_PLAN, RESULT as DOWNLOAD_RESULT
from capture import DATA, HERE, ROOT, now, sha, verify, write
from book_replay import Book, nanoseconds

PLAN = HERE / 'audit_registration.json'
RESULT = HERE / 'audit_result.json'
FUNDING = HERE.parent / 'historical_funding_clock_20261106/audit_result.json'
FROZEN = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
NS_PER_MS = 10**6
NS_PER_SECOND = 10**9
HEADER = ('received_time', 'event_time', 'transaction_time', 'symbol', 'event_type',
          'first_update_id', 'final_update_id', 'prev_final_update_id', 'last_update_id')
LEVEL = ('side', 'price', 'quantity', 'order_count')


def receipt_body(row):
    path = ROOT / row['path']
    if path.stat().st_size != row['bytes'] or sha(path.read_bytes()) != row['sha256']:
        raise ValueError('Download hash mismatch: ' + row['id'])
    return path


def register():
    downloads = json.loads(DOWNLOAD_RESULT.read_text())
    verify(json.loads(DOWNLOAD_PLAN.read_text()))
    if downloads['registration_sha256'] != sha(DOWNLOAD_PLAN.read_bytes()):
        raise ValueError('Download registration hash mismatch')
    funding = json.loads(FUNDING.read_text())
    reference = funding['book_samples'][0]
    ticker = ROOT / reference['normalized_bbo']
    if sha(ticker.read_bytes()) != reference['normalized_bbo_sha256']:
        raise ValueError('Prior quote reference hash mismatch')
    for row in downloads['receipts']:
        receipt_body(row)
    inputs = (Path(__file__), DATA / 'book_replay.py', DOWNLOAD_PLAN, DOWNLOAD_RESULT,
              FUNDING, ticker, FROZEN)
    write(PLAN, dict(registered_at_utc=now(), pyarrow_version=pa.__version__,
                     input_hashes={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in inputs},
                     cutoff_minutes=list(range(5, 60, 5)), reference_quotes=str(ticker.relative_to(ROOT)),
                     snapshot_policy='Ignore possible synthetic REST timestamps. Hourly checkpoints '
                                     'may bridge sequences, but cannot certify the original depth boundary.',
                     reference_policy='Same symbol and exact sequence ID, with each collector clock retained.',
                     depth_policy='Both edges must be inside a raw REST snapshot with a verified '
                                  'subsequent sequence bridge. Equal or regressing receipt clocks '
                                  'are never filled from event time. Cutoffs use inclusive receipt time.',
                     financial_accounts=0))


def parquet_rows(parquet):
    for batch in parquet.iter_batches(batch_size=65536, columns=[*HEADER, *LEVEL]):
        columns = batch.to_pydict()
        yield from zip(*(columns[key] for key in (*HEADER, *LEVEL)), strict=True)


def messages(parquet):
    for key, rows in groupby(parquet_rows(parquet), key=lambda row: row[:len(HEADER)]):
        changes = {'bid': [], 'ask': []}
        count, noop = 0, 0
        for row in rows:
            side, price, quantity, order_count = row[len(HEADER):]
            count += 1
            if order_count is not None:
                raise ValueError('Unexpected Binance order count')
            if side == 'noop' and price == '0' and quantity == '0':
                noop += 1
            elif side in changes:
                changes[side].append((price, quantity))
            else:
                raise ValueError('Unknown book side or malformed noop')
        if noop and count != 1:
            raise ValueError('Noop mixed with price levels')
        yield key, changes, count


def snapshot_kind(market, received, event, transaction, hour_start):
    if received == hour_start and transaction is None:
        return 'hour_checkpoint'
    if market == 'futures' and transaction is not None and received > event * NS_PER_MS:
        return 'rest_snapshot'
    if market == 'spot' and received % NS_PER_MS and received // NS_PER_MS == event:
        return 'rest_snapshot'
    return 'unverified_snapshot_clock'


def read_reference(plan):
    quotes = defaultdict(lambda: defaultdict(list))
    with (ROOT / plan['reference_quotes']).open() as handle:
        for line in handle:
            quote = json.loads(line)
            quotes[quote['symbol']][quote['update_id']].append(quote)
    funding = defaultdict(list)
    for row in json.loads(FUNDING.read_text())['funding']:
        funding[row['symbol']].append(row['first_sampled_receive_ns'])
    return quotes, {symbol: min(times) for symbol, times in funding.items()}


def replay(row, plan, references, funding):
    path = receipt_body(row)
    parquet = pq.ParquetFile(path)
    if tuple(parquet.schema_arrow.names) != (*HEADER, *LEVEL):
        raise ValueError('Unexpected Parquet schema')
    start = nanoseconds(f"{row['day']}T{row['hour']:02d}:00:00Z")
    end = start + 3600 * NS_PER_SECOND
    market, symbol = row['market'], row['symbol']
    use_reference = row['day'] == '2026-11-01' and market == 'futures'
    quotes = references[symbol] if use_reference else {}
    cutoffs = {start + minute * 60 * NS_PER_SECOND: f'minute_{minute:02d}'
               for minute in plan['cutoff_minutes']}
    if use_reference:
        cutoffs[funding[symbol]] = 'first_tardis_sampled_funding_receipt'
    ordered_cutoffs = iter(sorted(cutoffs))
    next_cutoff = next(ordered_cutoffs, end)
    book = Book(market)
    counts, errors, snapshots, checks, comparisons = Counter(), [], [], {}, []
    anchor, latest, previous_received = None, None, start
    message_ns_min, message_ns_max, lag_min, lag_max = None, None, None, None
    largest_gap_ns = 0

    def observe():
        if latest is None:
            return None
        quote = book.observation()
        if quote is None:
            return None
        if anchor['kind'] != 'rest_snapshot':
            quote.update(bbo_within_known_snapshot_region=None, bid_band_complete=False,
                         ask_band_complete=False)
        return dict(quote, collector_received_ns=latest[0], exchange_event_ms=latest[1],
                    exchange_transaction_ms=latest[2], anchor=anchor)

    for key, changes, level_count in messages(parquet):
        received, event, transaction, message_symbol, kind, first, final, prev, last = key
        counts['rows'] += level_count
        counts[kind] += 1
        if (message_symbol != symbol or not start <= received < end or received < previous_received
                or type(event) is not int or type(final) is not int or final <= 0):
            raise ValueError(f'Schema, identity or receipt chronology error at level row {counts["rows"]}')
        largest_gap_ns = max(largest_gap_ns, received - previous_received)
        previous_received = received
        message_ns_min = received if message_ns_min is None else message_ns_min
        message_ns_max = received
        while next_cutoff < received:
            try:
                obs = observe()
            except ValueError as exc:
                obs = None
                counts['crossed_at_cutoff'] += 1
                errors.append(dict(at=next_cutoff, error=str(exc)))
            checks[cutoffs[next_cutoff]] = dict(cutoff_ns=next_cutoff, book=obs)
            next_cutoff = next(ordered_cutoffs, end)

        if kind == 'snapshot':
            if first is not None or prev is not None or last != final:
                raise ValueError('Unexpected snapshot sequence fields')
            snapshot = snapshot_kind(market, received, event, transaction, start)
            counts[snapshot] += 1
            snapshots.append(dict(kind=snapshot, collector_received_ns=received, source_event_ms=event,
                                  exchange_transaction_ms=transaction, last_update_id=final, levels=level_count))
            if snapshot == 'unverified_snapshot_clock':
                continue
            book.reset()
            latest = None
            anchor = snapshots[-1]
            if not changes['bid'] and not changes['ask']:
                counts['empty_snapshot'] += 1
                continue
            book.snapshot(dict(lastUpdateId=final, bids=changes['bid'], asks=changes['ask']))
            continue
        if kind != 'update' or type(first) is not int or first > final or last is not None:
            raise ValueError('Unexpected update identity or sequence')
        if market == 'futures' and (type(prev) is not int or type(transaction) is not int):
            raise ValueError('Futures update is missing pu or exchange transaction time')
        if market == 'spot' and (prev is not None or transaction is not None):
            raise ValueError('Spot update has undocumented pu or transaction time')
        lag = received - event * NS_PER_MS
        lag_min = lag if lag_min is None else min(lag_min, lag)
        lag_max = lag if lag_max is None else max(lag_max, lag)
        if lag < 0 or (transaction is not None and transaction > event):
            counts['update_clock_conflict'] += 1
            book.reset()
            latest = None
            continue
        if book.last is None:
            counts['updates_without_anchor'] += 1
            continue
        try:
            if market == 'futures' and not book.ready and prev == book.last and final > book.last:
                # Provider can omit the update already included in the snapshot.
                book.ready = True
            applied = book.update(dict(U=first, u=final, pu=prev, b=changes['bid'], a=changes['ask']))
        except ValueError as exc:
            counts['sequence_or_level_errors'] += 1
            if len(errors) < 20:
                errors.append(dict(at=received, error=str(exc), update_id=final))
            book.reset()
            latest = None
            continue
        if not applied:
            counts['obsolete_updates'] += 1
            continue
        counts['applied_updates'] += 1
        latest = (received, event, transaction)
        if final in quotes:
            try:
                obs = observe()
            except ValueError:
                counts['crossed_on_reference_join'] += 1
                continue
            if obs is None:
                continue
            for ref in quotes[final]:
                price_match = all(Decimal(ref[key]) == Decimal(obs[key]) for key in ('bid', 'ask'))
                clock_match = event == ref['exchange_event_ms'] and transaction == ref['exchange_transaction_ms']
                counts['same_id_reference_rows'] += 1
                counts['same_id_price_matches'] += int(price_match)
                counts['same_id_exchange_clock_matches'] += int(clock_match)
                if len(comparisons) < 8 or (not price_match and len(comparisons) < 16):
                    comparisons.append(dict(update_id=final, price_match=price_match,
                        exchange_clock_match=clock_match, local_receive_ns=received,
                        tardis_receive_ns=ref['collector_received_ns'],
                        local_bbo=[obs['bid'], obs['ask']], tardis_bbo=[ref['bid'], ref['ask']]))
    while next_cutoff < end:
        # No tail carry beyond the last received message in the archived hour.
        checks[cutoffs[next_cutoff]] = dict(cutoff_ns=next_cutoff, book=None)
        next_cutoff = next(ordered_cutoffs, end)
    if counts['rows'] != parquet.metadata.num_rows:
        raise ValueError('Parquet footer row count does not match decoded messages')
    return dict(id=row['id'], rows=counts['rows'], counts=dict(counts), snapshots=snapshots,
                error_examples=errors, observations=checks, reference_examples=comparisons,
                schema={field.name: str(field.type) for field in parquet.schema_arrow},
                first_received_ns=message_ns_min, last_received_ns=message_ns_max,
                largest_receive_gap_ns=largest_gap_ns, update_lag_min_ns=lag_min, update_lag_max_ns=lag_max,
                independent_bbo_reference_rows=sum(map(len, quotes.values())))


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if pa.__version__ != plan['pyarrow_version'] or RESULT.exists():
        raise ValueError('Runtime version changed or result already exists')
    quotes, funding = read_reference(plan)
    receipts = json.loads(DOWNLOAD_RESULT.read_text())['receipts']
    results = []
    for row in receipts:
        if (row.get('http_status'), row.get('state')) != (200, 'body_complete'):
            raise ValueError('Incomplete sample download')
        try:
            result = replay(row, plan, quotes, funding)
        except (ValueError, TypeError, ArithmeticError) as exc:
            result = dict(id=row['id'], audit_error=f'{type(exc).__name__}: {exc}')
        results.append(result)
        print(row['id'], result.get('rows'), result.get('audit_error'), flush=True)
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    verify(plan)
    write(RESULT, dict(registration_sha256=sha(PLAN.read_bytes()), results=results,
                       frozen_financial_inputs_verified=len(frozen['input_hashes']),
                       historical_depth_admission=False, financial_accounts=0))


if __name__ == '__main__':
    if sys.argv[1:] == ['register']:
        register()
    elif sys.argv[1:] == ['run']:
        run()
    else:
        raise SystemExit('Use register or run')
