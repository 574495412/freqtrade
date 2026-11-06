"""Recompute calendar, collector and modeled-fill evidence from pinned sources."""
from collections import Counter
import json
from pathlib import Path

from audit import HERE, ROOT, LONG, SHORT, PLAN, RESULT, CAPTURE_PLAN, CAPTURE_RESULT
from audit import archived_fill_screen, build_calendar, collector_evidence, get, now, sha, verify, write


def audit():
    registration = get(PLAN)
    capture = get(CAPTURE_PLAN)
    manifest = get(CAPTURE_RESULT)
    result = get(RESULT)
    verify(registration)
    verify(capture)
    if (result['registration_sha256']!=sha(PLAN.read_bytes())
            or manifest['registration_sha256']!=sha(CAPTURE_PLAN.read_bytes())):
        raise ValueError('Funding receipt registration mismatch')
    calendar,native,official = build_calendar(manifest['receipts'],capture['symbols'])
    collector = collector_evidence(manifest['receipts'],official)
    fills = archived_fill_screen(native)
    if (json.loads(json.dumps(calendar)),collector,fills)!=(
            result['calendar'],result['collector'],result['saved_model_fill_screen']):
        raise ValueError('Funding evidence is not reproducible')
    rows = {}
    for window,events in calendar.items():
        if set(events)!=set(capture['symbols']) or any(
                row[key] for row in events.values() for key in (
                    'original_vs_native_missing_hours','native_only_hours','differing_native_rate_hours')):
            raise ValueError('Original and frozen event calendar or rate differs')
        if window=='S' and any(
                row[key] for row in events.values() for key in (
                    'current_only_times','frozen_only_times','changed_rate_at_exact_time')):
            raise ValueError('New short-window public response differs from frozen originals')
        offsets = sum((Counter(row['milliseconds_after_native_hour']) for row in events.values()),Counter())
        saved = [row for row in fills if row['window']==window]
        fill_counts = sum((Counter(row['counts']) for row in saved),Counter())
        rows[window] = dict(symbols=len(events),public_events=sum(row['events'] for row in events.values()),
                            events_after_native_hour=sum(v for k,v in offsets.items() if k>0),
                            maximum_offset_ms=max(offsets), archived_accounts=len(saved),
                            saved_model_fill_counts=dict(fill_counts),
                            affected_unique_public_identities=len({(fill['symbol'],fill['original_public_funding_ms'])
                                for row in saved for fill in row['boundary_fills']}),
                            affected_sides=sorted({fill['side'] for row in saved for fill in row['boundary_fills']}))
    if collector['missing_identities'] or not all(
            row['all_official_fields_match'] for row in collector['records']):
        raise ValueError('Same-day collector population not fully matched')
    delays = [row['first_observed_poll_delay_ns'] for row in collector['records']]
    frozen = get(LONG)
    verify(frozen)
    return dict(observed_at_utc=now(), windows=rows, source_capture_responses=len(manifest['receipts']),
                exact_short_official_matches=sum(row['current_exact_time_and_rate_matches']
                                                for row in calendar['S'].values()),
                collector_distinct_symbols=len({row['symbol'] for row in collector['records']}),
                collector_same_day_settlements=len(delays),
                collector_same_day_observations=sum(row['observations'] for row in collector['records']),
                collector_first_observed_poll_delay_ns=dict(min=min(delays),max=max(delays)),
                input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes())
                              for path in (Path(__file__),RESULT,PLAN,CAPTURE_RESULT,LONG,SHORT)},
                frozen_financial_inputs_verified=len(frozen['input_hashes']),
                historical_account_settlement_order_verified=False, financial_accounts=0)


if __name__=='__main__':
    write(HERE/'verification.json',audit())
