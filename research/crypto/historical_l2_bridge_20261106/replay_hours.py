"""Carry a BTCUSDT futures book across contiguous, registered UTC partitions."""
from collections import Counter
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq

from capture_hours import HERE, PRIOR, ROOT, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from audit import HEADER, LEVEL, NS_PER_MS, NS_PER_SECOND, messages, receipt_body, snapshot_kind
from capture import DATA, now, sha, verify, write
from book_replay import Book, levels, nanoseconds

PLAN = HERE / 'replay_registration.json'
RESULT = HERE / 'replay_result.json'
FROZEN = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
HOUR = 3600 * NS_PER_SECOND


def hour_ns(row):
    return nanoseconds(f"{row['day']}T{row['hour']:02d}:00:00Z")


def register():
    capture = json.loads(CAPTURE_PLAN.read_text())
    verify(capture)
    manifest = json.loads(CAPTURE_RESULT.read_text())
    if manifest['registration_sha256'] != sha(CAPTURE_PLAN.read_bytes()):
        raise ValueError('Capture registration hash mismatch')
    rows = sorted([capture['target'], *manifest['receipts']], key=hour_ns)
    if len(rows) != 7 or any(hour_ns(b) - hour_ns(a) != HOUR for a, b in zip(rows, rows[1:])):
        raise ValueError('Expected seven contiguous partitions')
    paths = [receipt_body(row) for row in rows if row.get('state') == 'body_complete']
    inputs = (Path(__file__), PRIOR / 'audit.py', DATA / 'book_replay.py',
              CAPTURE_PLAN, CAPTURE_RESULT, FROZEN, *paths)
    write(PLAN, dict(registered_at_utc=now(), pyarrow_version=pa.__version__,
                     rows=rows, cutoff_minutes=list(range(5, 60, 5)),
                     snapshot_policy='Raw exchange and collector clocks must be ordered and distinct. '
                                     'An hour checkpoint can anchor updates, with no raw depth boundary. '
                                     'Preserve a prior raw anchor across a matching checkpoint only when '
                                     'the ID and all levels inside both original boundaries agree.',
                     bridge_policy='Preserve a prior book across exact contiguous hours only if the next '
                                   'advancing update has pu equal to the prior sequence. Missing partitions, '
                                   'gaps and clock conflicts clear the state. Never carry beyond a file tail.',
                     input_hashes={str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs},
                     financial_accounts=0))


class Replay:
    def __init__(self):
        self.book = Book('futures')
        self.anchor = None
        self.latest = None
        self.previous_update = None

    def reset_book(self):
        self.book.reset()
        self.anchor = self.latest = None

    def apply_snapshot(self, changes, stamp, counts):
        book = self.book
        if stamp['kind'] == 'unverified_snapshot_clock':
            return 'excluded_clock'
        if book.last is not None and stamp['id'] < book.last:
            return 'obsolete_snapshot'
        if not changes['bid'] and not changes['ask']:
            self.reset_book()
            return 'empty_snapshot_reset'
        if (stamp['kind'] == 'hour_checkpoint' and book.ready and self.anchor['kind'] == 'rest_snapshot'
                and stamp['id'] == book.last):
            checks = (
                (book.bids, levels(changes['bid']), book.bid_boundary, True),
                (book.asks, levels(changes['ask']), book.ask_boundary, False),
            )
            same = all(
                {price: qty for price, qty in old.items() if (price >= edge if bid else price <= edge)}
                == {price: qty for price, qty in new.items() if (price >= edge if bid else price <= edge)}
                for old, new, edge, bid in checks
            )
            if same:
                return 'raw_anchor_preserved'
            counts['checkpoint_disagreement'] += 1
        self.reset_book()
        book.snapshot(dict(lastUpdateId=stamp['id'], bids=changes['bid'], asks=changes['ask']))
        self.anchor = dict(stamp, bid_boundary=str(book.bid_boundary), ask_boundary=str(book.ask_boundary))
        return 'accepted'

    def observe(self):
        if self.latest is None:
            return None
        observation = self.book.observation()
        if observation is None:
            return None
        if self.anchor['kind'] != 'rest_snapshot':
            observation.update(bbo_within_known_snapshot_region=None, bid_band_complete=False,
                               ask_band_complete=False)
        return dict(observation, update=self.latest, anchor=self.anchor)

    def partition(self, row, cutoff_minutes):
        start = hour_ns(row)
        end = start + HOUR
        cutoffs = iter(start + minute * 60 * NS_PER_SECOND for minute in cutoff_minutes)
        next_cutoff = next(cutoffs, end)
        out = dict(id=row['id'], start_ns=start, counts={}, observations=[], snapshots=[],
                   faults=[], previous_partition_last_update=self.previous_update,
                   previous_partition_book_sequence=self.book.last)
        if (row.get('http_status'), row.get('state')) != (200, 'body_complete'):
            self.reset_book()
            self.previous_update = None
            return dict(out, status='partition_unavailable', http_status=row.get('http_status'))
        parquet = pq.ParquetFile(receipt_body(row))
        if tuple(parquet.schema_arrow.names) != (*HEADER, *LEVEL):
            raise ValueError('Unexpected Parquet schema')
        counts = Counter()
        last_received = start
        first_advancing_update = True
        for key, changes, n in messages(parquet):
            received, event, transaction, symbol, kind, first, final, prev, last = key
            counts['rows'] += n
            counts[kind] += 1
            if (symbol != row['symbol'] or not start <= received < end or received < last_received
                    or type(event) is not int or type(final) is not int or final <= 0):
                raise ValueError(f"Invalid receipt order or identity in {row['id']} at row {counts['rows']}")
            last_received = received
            while next_cutoff < received:
                try:
                    observation = self.observe() if 'first_applied_update' in out else None
                except ValueError:
                    observation = None
                    counts['crossed_at_cutoff'] += 1
                    self.reset_book()
                out['observations'].append(dict(cutoff_ns=next_cutoff, book=observation))
                next_cutoff = next(cutoffs, end)
            stamp = dict(id=final, received_ns=received, event_ms=event, transaction_ms=transaction,
                         partition=row['id'], level_rows=n)
            if kind == 'snapshot':
                if last != final or first is not None or prev is not None:
                    raise ValueError('Invalid snapshot sequence fields')
                anchor_kind = snapshot_kind('futures', received, event, transaction, start)
                if transaction is not None and transaction > event:
                    anchor_kind = 'unverified_snapshot_clock'
                stamp['kind'] = anchor_kind
                status = self.apply_snapshot(changes, stamp, counts)
                counts[anchor_kind] += 1
                out['snapshots'].append(dict(stamp, status=status))
                continue
            if (kind != 'update' or type(first) is not int or first > final or last is not None
                    or type(prev) is not int or type(transaction) is not int):
                raise ValueError('Invalid update identity')
            stamp.update(first=first, prev=prev)
            if 'first_update' not in out:
                out['first_update'] = stamp
                previous = out['previous_partition_last_update']
                out['cross_hour_update_pu_matches'] = None if previous is None else prev == previous['id']
            if self.previous_update is not None and prev != self.previous_update['id']:
                counts['raw_update_pu_discontinuity'] += 1
            self.previous_update = stamp
            if not transaction <= event or received < event * NS_PER_MS:
                counts['update_clock_conflict'] += 1
                self.reset_book()
                continue
            book = self.book
            if book.last is None:
                counts['updates_without_anchor'] += 1
                continue
            if first_advancing_update and final > book.last:
                first_advancing_update = False
                if self.anchor['partition'] != row['id'] and prev != book.last:
                    counts['carry_rejected_pu'] += 1
                    self.reset_book()
                    continue
            try:
                if not book.ready and final > book.last and prev == book.last:
                    book.ready = True
                applied = book.update(dict(U=first, u=final, pu=prev, b=changes['bid'], a=changes['ask']))
            except ValueError as exc:
                counts['sequence_or_level_fault'] += 1
                if len(out['faults']) < 12:
                    out['faults'].append(dict(stamp, error=str(exc)))
                self.reset_book()
                continue
            if applied:
                counts['applied_updates'] += 1
                self.latest = stamp
                out.setdefault('first_applied_update', stamp)
            else:
                counts['obsolete_updates'] += 1
        while next_cutoff < end:
            out['observations'].append(dict(cutoff_ns=next_cutoff, book=None))
            next_cutoff = next(cutoffs, end)
        if counts['rows'] != parquet.metadata.num_rows:
            raise ValueError('Parquet row count mismatch')
        if not counts['rows']:
            self.reset_book()
            self.previous_update = None
        out.update(status='processed', counts=dict(counts), last_update=self.previous_update,
                   ending_book_sequence=self.book.last, ending_anchor=self.anchor,
                   ending_applied_update=self.latest)
        return out


def run():
    plan = json.loads(PLAN.read_text())
    verify(plan)
    if pa.__version__ != plan['pyarrow_version'] or RESULT.exists():
        raise ValueError('Replay runtime changed or result already exists')
    replay = Replay()
    results = []
    for row in plan['rows']:
        result = replay.partition(row, plan['cutoff_minutes'])
        results.append(result)
        print(row['id'], result['status'], result['counts'], flush=True)
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
