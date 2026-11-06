"""Revalidate retained public bodies and compare the rules at observed capture times."""
from base64 import b32encode
from collections import Counter
from decimal import Decimal
from hashlib import sha1
import json
from pathlib import Path

from capture_snapshots import INDEX, REGISTRATION, RESULT, inspect
from probe_index import HERE, ROOT, decode, now, sha, verify, write

NUMERIC_FIELDS = ('tickSize', 'minPrice', 'maxPrice', 'stepSize', 'minQty', 'maxQty', 'minNotional')
MARKET_LOT_FIELDS = ('stepSize', 'minQty', 'maxQty')


def check_receipt(row):
    raw = (ROOT / row['path']).read_bytes()
    if len(raw) != row['bytes'] or sha(raw) != row['sha256']:
        raise ValueError('Raw body mismatch: ' + row['id'])
    return raw


def compare(left, right):
    changes = {}
    common = left['complete_rules'].keys() & right['complete_rules'].keys()
    for pair in sorted(common):
        before, after = (row['complete_rules'][pair] for row in (left, right))
        changed = {}
        for key in NUMERIC_FIELDS:
            if Decimal(str(before[key])) != Decimal(str(after[key])):
                changed[key] = [before[key], after[key]]
        for key in ('status', 'notional_filter'):
            if before[key] != after[key]:
                changed[key] = [before[key], after[key]]
        for key in MARKET_LOT_FIELDS:
            values = [(entry['market_lot_size'] or {}).get(key) for entry in (before, after)]
            numbers = [Decimal(str(value)) if value is not None else None for value in values]
            if numbers[0] != numbers[1]:
                changed['market_lot_size.' + key] = values
        if changed:
            changes[pair] = changed
    return dict(before=left['id'], after=right['id'], common_pairs=len(common),
                changed_pairs_by_field=dict(Counter(key for row in changes.values() for key in row)),
                changes=changes)


def audit():
    index = json.loads(INDEX.read_text())
    plan = json.loads(REGISTRATION.read_text())
    result = json.loads(RESULT.read_text())
    if result['registration_sha256'] != sha(REGISTRATION.read_bytes()):
        raise ValueError('Snapshot registration hash mismatch')
    verify(plan)
    for row in index['rows']:
        check_receipt(row)

    by_market = {market: [] for market in plan['universe']}
    points = []
    if [row['id'] for row in result['rows']] != [case['id'] for case in plan['cases']]:
        raise ValueError('Snapshot receipts do not match registered case order')
    for row, case in zip(result['rows'], plan['cases'], strict=True):
        if any(row[key] != value for key, value in case.items()):
            raise ValueError('Receipt case differs from registration')
        raw = check_receipt(row)
        body = decode(raw, row['headers'])
        matches = [name for name, data in [('raw', raw), ('decoded', body)]
                   if b32encode(sha1(data).digest()).decode() == case['digest']]
        if not matches or not row['memento_matches'] or row['effective_url'] != case['url']:
            raise ValueError('Archive provenance mismatch')
        extracted = inspect(body, row['market'], plan['universe'][row['market']])
        if any(row[key] != value for key, value in extracted.items()):
            raise ValueError('Stored rules differ from replay body')
        points.append(dict(id=row['id'], market=row['market'], window=row['window'],
                           pairs=len(row['complete_rules']), cdx_digest_matched_on=matches))
        by_market[row['market']].append(row)

    frozen_plan = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'
    frozen = json.loads(frozen_plan.read_text())
    verify(frozen)
    comparisons = [compare(left, right) for rows in by_market.values()
                   for left, right in zip(rows, rows[1:])]
    return dict(
        observed_at_utc=now(), index_receipts_checked=len(index['rows']),
        snapshot_receipts_checked=len(result['rows']),
        frozen_financial_inputs_checked=len(frozen['input_hashes']),
        input_hashes={str(p.relative_to(ROOT)): sha(p.read_bytes())
                      for p in (Path(__file__), INDEX, REGISTRATION, RESULT, frozen_plan)},
        verified_points=points, symbol_point_observations=sum(p['pairs'] for p in points),
        consecutive_point_differences=comparisons, continuous_effective_intervals_verified=0,
        historical_rule_admission=False, financial_accounts=0,
    )


if __name__ == '__main__':
    write(HERE / 'verification.json', audit())
