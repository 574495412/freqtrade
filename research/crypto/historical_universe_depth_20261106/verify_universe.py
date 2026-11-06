"""Independently re-run two fixed-cutoff replays and reconcile the per-pair counts."""
from collections import defaultdict
import json
from pathlib import Path

from audit_universe import HERE, ROOT, FROZEN, SECOND, MINUTE, nanoseconds, summarize
from audit_universe import PLAN as FIRST_PLAN, RESULT as FIRST_RESULT
from capture_universe import ROADMAP, PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture_universe import now, sha, verify, write
from exact_predecessor import PLAN as SECOND_PLAN, RESULT as SECOND_RESULT, recover


def read(registration, output):
    plan = json.loads(registration.read_text())
    verify(plan)
    result = json.loads(output.read_text())
    if result['registration_sha256'] != sha(registration.read_bytes()):
        raise ValueError('Evidence registration mismatch')
    return plan, result


def audit():
    plan, receipts = read(CAPTURE_PLAN,CAPTURE_RESULT)
    _, first = read(FIRST_PLAN,FIRST_RESULT)
    targets, second = read(SECOND_PLAN,SECOND_RESULT)
    groups, captures = defaultdict(dict), {}
    for receipt in receipts['receipts']:
        if (receipt['http_status'],receipt['response_state']) != (200,'body_complete'):
            raise ValueError('Incomplete source response')
        path = ROOT/receipt['path']
        if path.stat().st_size != receipt['bytes'] or sha(path.read_bytes()) != receipt['sha256']:
            raise ValueError('Raw capture mismatch')
        groups[receipt['group']][receipt['kind']] = receipt
        captures[receipt['id']] = receipt

    recomputed = defaultdict(list)
    for group in groups.values():
        row = group['depth']
        cohort = row['market']+'_'+row['window']
        cutoff = nanoseconds(row['day']+'T00:00:00Z')+plan['cutoff_minute']*MINUTE
        recomputed[cohort].extend(summarize(group,cutoff,plan['liquidity_freshness_seconds']*SECOND))
    if dict(recomputed) != first['pair_points']:
        raise ValueError('Initial market replay is not reproducible')
    if [row['pair'] for row in targets['targets']] != [row['pair'] for row in second['rows']]:
        raise ValueError('Predecessor target mismatch')
    direct = {}
    for target,row in zip(targets['targets'],second['rows'],strict=True):
        rebuilt = recover(target,captures[target['depth_capture']],captures[target['bbo_capture']])
        if dict(target,**rebuilt) != row:
            raise ValueError('Exact predecessor replay is not reproducible')
        direct[(target['cohort'],target['pair'])] = rebuilt

    universe = json.loads(ROADMAP.read_text())['markets']
    coverage, gaps, disagreements = {}, [], []
    for cohort, rows in recomputed.items():
        if [row['pair'] for row in rows] != universe[cohort.split('_')[0]]['pairs']:
            raise ValueError('Frozen pair order or membership changed')
        combined = []
        for row in rows:
            patch = direct.get((cohort,row['pair']))
            initial = row['depth']['observation']
            if patch is not None:
                if initial is not None:
                    raise ValueError('Predecessor unexpectedly replaces an admitted v1 observation')
                final = patch['observation']
                fresh, full, same, known = (patch[key] for key in ('clocks_fresh','both_sides_20bps_complete',
                                                                  'same_id_prices_match','fresh_known_bbo'))
                ticker = patch['same_id_ticker']
            else:
                final = initial
                fresh, full, same, known = (row[key] for key in ('depth_clocks_fresh','both_sides_20bps_complete',
                                                                'same_id_prices_match','bid_ask_within_snapshot'))
                ticker = row['same_id_ticker']
            if not full:
                gaps.append(dict(cohort=cohort, pair=row['pair'], has_fresh_bbo=known,
                                 book=final, snapshot=None if patch else row['depth']['anchor']))
            if ticker and not same:
                disagreements.append(dict(cohort=cohort, pair=row['pair'], depth=final, book_ticker=ticker))
            combined.append((bool(final),fresh,full,same,known))
        coverage[cohort] = dict(
            frozen_pairs=len(rows), valid_received_snapshots=first['checks'][cohort]['captured_rest_snapshot'],
            replayed_at_cutoff=sum(x[0] for x in combined),
            fresh_bbo_inside_known_snapshot=sum(x[4] for x in combined),
            fresh_complete_both_sides_20bps=sum(x[2] for x in combined),
            bbo_exact_id_and_prices_match=sum(x[3] for x in combined),
            complete_depth_and_bbo_exact_id_match=sum(x[2] and x[3] for x in combined),
            separately_received_fresh_book_ticker=sum(row['latest_ticker_receive_fresh'] for row in rows))
    frozen = json.loads(FROZEN.read_text())
    verify(frozen)
    return dict(observed_at_utc=now(), capture_bodies_verified=len(captures),
                source_bytes_verified=sum(row['bytes'] for row in captures.values()),
                input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes())
                              for path in (Path(__file__),CAPTURE_RESULT,FIRST_RESULT,SECOND_RESULT,FROZEN)},
                exact_predecessor_recoveries=len(direct), coverage=coverage,
                frozen_pair_points=sum(row['frozen_pairs'] for row in coverage.values()),
                fresh_bbo_points=sum(row['fresh_bbo_inside_known_snapshot'] for row in coverage.values()),
                complete_both_sides_points=sum(row['fresh_complete_both_sides_20bps'] for row in coverage.values()),
                same_id_bbo_matches=sum(row['bbo_exact_id_and_prices_match'] for row in coverage.values()),
                incomplete_20bps=gaps, same_id_price_disagreements=disagreements,
                frozen_financial_inputs_verified=len(frozen['input_hashes']),
                full_window_depth_admission=False, financial_accounts=0)


if __name__=='__main__':
    write(HERE/'verification.json',audit())
