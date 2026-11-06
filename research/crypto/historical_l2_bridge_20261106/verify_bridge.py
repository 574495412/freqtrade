"""Verify raw snapshot twins, cross-hour identity and the unchanged financial inputs."""
from decimal import Decimal
import json
from pathlib import Path

import pyarrow.parquet as pq

from replay_hours import PLAN, RESULT, FROZEN, HERE, ROOT, messages, now, receipt_body, sha, verify, write


def audit():
    plan = json.loads(PLAN.read_text())
    report = json.loads(RESULT.read_text())
    if report['registration_sha256'] != sha(PLAN.read_bytes()):
        raise ValueError('Replay registration hash mismatch')
    verify(plan)
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    cases = plan['rows']
    results = report['results']
    if [row['id'] for row in cases] != [row['id'] for row in results]:
        raise ValueError('Case order drift')
    paths = [receipt_body(row) for row in cases]
    if any(row['status'] != 'processed' for row in results):
        raise ValueError('Replay partition was not processed')
    bridges = []
    for previous, following in zip(results, results[1:]):
        first = following['first_applied_update']
        tail = previous['ending_applied_update']
        if (tail['id'] != first['prev'] or tail['received_ns'] >= first['received_ns']
                or not following['cross_hour_update_pu_matches']):
            raise ValueError('Unverified cross-hour bridge')
        bridges.append(dict(from_partition=previous['id'], to_partition=following['id'],
                            previous_update=tail, next_update=first))
    target = next(row for row in results if row['id'] == 'futures-2026-08-31-BTCUSDT-download')
    if target['counts']['applied_updates'] != target['counts']['update'] or target['snapshots']:
        raise ValueError('Original no-anchor file did not replay from its predecessor')
    snapshots = [item for item in messages(pq.ParquetFile(paths[0])) if item[0][4] == 'snapshot']
    (earlier, a, na), (later, b, nb) = snapshots
    if earlier[1:] != later[1:] or a != b or na != nb or earlier[0] != earlier[1] * 10**6:
        raise ValueError('The synthetic-clock and collector-clock copies do not match')
    if later[0] <= earlier[0] or later[2] > later[1]:
        raise ValueError('Invalid snapshot receipt time')
    bids = [Decimal(p) for p, _ in b['bid']]
    asks = [Decimal(p) for p, _ in b['ask']]
    min_bid_for_full = min(bids) / Decimal('.998')
    max_ask_for_full = max(asks) / Decimal('1.002')
    points = [item['book'] for row in results for item in row['observations']]
    if any(point is None or point['anchor']['id'] != later[8] for point in points):
        raise ValueError('A sample point did not use the verified raw anchor')
    return dict(
        observed_at_utc=now(),
        input_hashes={str(path.relative_to(ROOT)): sha(path.read_bytes())
                      for path in (Path(__file__), PLAN, RESULT, FROZEN)},
        original_snapshot=dict(update_id=later[8], event_ms=later[1], transaction_ms=later[2],
            event_time_copy_ns=earlier[0], retained_collector_receive_ns=later[0],
            receive_delay_ns=later[0]-earlier[0], bid_levels=len(bids), ask_levels=len(asks),
            snapshot_best_bid=str(max(bids)), snapshot_best_ask=str(min(asks)),
            raw_bid_boundary=str(min(bids)), raw_ask_boundary=str(max(asks)),
            bid_at_least_for_complete_20bps=str(min_bid_for_full),
            ask_at_most_for_complete_20bps=str(max_ask_for_full),
            boundaries_can_cover_both_sides_20bps=(min_bid_for_full < max_ask_for_full)),
        bridges=bridges,
        total_price_rows=sum(row['counts']['rows'] for row in results),
        total_update_messages=sum(row['counts']['update'] for row in results),
        applied_update_messages=sum(row['counts']['applied_updates'] for row in results),
        target_applied_update_messages=target['counts']['applied_updates'],
        target_cutoffs_with_book=len(target['observations']),
        cutoffs_with_book=len(points),
        cutoffs_with_bbo_within_known_snapshot=sum(point['bbo_within_known_snapshot_region'] for point in points),
        cutoffs_with_complete_both_sides_20bps=sum(point['bid_band_complete'] and point['ask_band_complete']
                                                 for point in points),
        frozen_financial_inputs_verified=len(frozen['input_hashes']),
        historical_depth_admission=False,
        financial_accounts=0,
    )


if __name__ == '__main__':
    write(HERE / 'verification.json', audit())
