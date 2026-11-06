"""Verify archived joins and quantify candle fields affected by late trade receipts."""
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path

from audit_trades import FIVE_MINUTES, NS_PER_MS, OHLCV, SOURCE_PROBE
from audit_trades import PLAN as AUDIT_PLAN, RESULT as AUDIT_RESULT
from audit_trades import collector, join, official_rows, start_ns
from capture_trades import HERE, ROOT, FUTURES_L
from capture_trades import PLAN as CAPTURE_PLAN, RESULT as CAPTURE_RESULT
from capture import now, sha, verify, write
from probe_event_sources import decode
from successor import PLAN as SUCCESSOR_PLAN, RESULT as SUCCESSOR_RESULT


def read_manifest(registration, result):
    plan = json.loads(registration.read_text())
    verify(plan)
    value = json.loads(result.read_text())
    if value['registration_sha256'] != sha(registration.read_bytes()):
        raise ValueError('Manifest registration mismatch')
    return plan, value


def kline_channels():
    channels = {}
    for row in json.loads(SOURCE_PROBE.read_text())['probes']:
        if row['kind'] != 'metadata' or row['http_status'] != 200:
            continue
        path = ROOT / row['raw']['path']
        raw = path.read_bytes()
        if sha(raw) != row['raw']['sha256']:
            raise ValueError('Historical metadata probe hash mismatch')
        advertised = json.loads(decode(raw, row['headers']))['availableChannels']
        channels[row['id']] = dict(url=row['url'], raw_sha256=row['raw']['sha256'],
                                   channels=advertised,
                                   kline_advertised=any('kline' in name.lower() for name in advertised))
    return channels


def aggregate(rows):
    prices = [price for price, _ in rows]
    return [prices[0], max(prices), min(prices), prices[-1], sum((q for _, q in rows), Decimal(0))]


def timing(rows, audit_plan, group_recovered):
    hour, archive, check = (rows[key] for key in ('collector', 'official_zip', 'official_checksum'))
    start = start_ns(hour)
    trades, _ = collector(hour)
    visible, complete = ([[] for _ in range(12)] for _ in range(2))
    late = [[] for _ in range(12)]
    for tid, at, price, quantity, maker in official_rows(archive, check, audit_plan, Counter()):
        i = (at - start)//FIVE_MINUTES
        end = start + (i+1)*FIVE_MINUTES
        if tid in group_recovered:
            known = group_recovered[tid]
        else:
            p, q, traded, side, event, known = trades[tid]
            if (p,q,traded,side)!=(price,quantity,at//NS_PER_MS,maker) or not traded<=event<=known//NS_PER_MS:
                raise ValueError('Constituent is missing verified matching trade')
        complete[i].append((price, quantity))
        if known < end:
            visible[i].append((price, quantity))
        else:
            late[i].append(known-end)

    result = []
    for i, (before, after, late_receipts) in enumerate(zip(visible, complete, late, strict=True)):
        final_fields, as_of = aggregate(after), aggregate(before)
        changed = [field for field, a, b in zip(OHLCV, as_of, final_fields, strict=True) if a != b]
        result.append(dict(end_ns=start+(i+1)*FIVE_MINUTES, constituents=len(after),
                           constituent_receipts_after_or_at_boundary=len(late_receipts),
                           last_late_receipt_after_boundary_ns=max(late_receipts,default=None),
                           changed_fields=changed, final_ohlcv=dict(zip(OHLCV,map(str,final_fields),strict=True)),
                           observed_trade_ohlcv_before_boundary=dict(zip(OHLCV,map(str,as_of),strict=True))))
    return result


def audit():
    capture, manifest = read_manifest(CAPTURE_PLAN, CAPTURE_RESULT)
    audit_plan, original = read_manifest(AUDIT_PLAN, AUDIT_RESULT)
    successor_plan, successor = read_manifest(SUCCESSOR_PLAN, SUCCESSOR_RESULT)
    found, _ = collector(successor['receipt'])
    missing = {row['trade_id']: row for row in successor_plan['missing']}
    recovered = {}
    for row in successor['recovered']:
        tid = row['trade_id']
        p,q,t,m,e,received = found[tid]
        expected = missing[tid]
        if ((p,q,t*NS_PER_MS,m) != (Decimal(expected['price']),Decimal(expected['quantity']),
                                   expected['exchange_trade_ns'],expected['is_buyer_maker'])
                or (e,received)!=(row['exchange_event_ms'],row['collector_received_ns'])):
            raise ValueError('Successor observation mismatch')
        recovered[tid] = received
    if recovered.keys() != missing.keys():
        raise ValueError('Boundary trade remains unmatched')
    groups = defaultdict(dict)
    for row in manifest['receipts']:
        groups[row['group']][row['source']] = row
    rows = {}
    for case in original['results']:
        key = case['id']
        if join(groups[key], capture['frozen_candles'][key], audit_plan) != case:
            raise ValueError('Nonreproducible trade audit')
        for bar in case['bars']:
            if not bar['price_fields_match'] or not bar['volume_matches']:
                raise ValueError('Frozen candle mismatch')
        rows[key] = timing(groups[key], audit_plan,
                           recovered if key==successor_plan['case']['group'] else {})
    all_bars = [bar for bars in rows.values() for bar in bars]
    frozen = json.loads(FUTURES_L.read_text())
    verify(frozen)
    return dict(
        observed_at_utc=now(),
        input_hashes={str(path.relative_to(ROOT)): sha(path.read_bytes())
                      for path in (Path(__file__), AUDIT_RESULT, SUCCESSOR_RESULT, SOURCE_PROBE, FUTURES_L)},
        historical_channel_metadata=kline_channels(),
        official_and_frozen_ohlcv_matching_bars=len(all_bars),
        bars_with_all_constituent_receipts=len(all_bars),
        matched_constituent_trades=sum(bar['constituents'] for bar in all_bars),
        constituents_observed_at_or_after_boundary=sum(
            bar['constituent_receipts_after_or_at_boundary'] for bar in all_bars),
        bars_with_at_least_one_late_constituent=sum(bool(bar['constituent_receipts_after_or_at_boundary'])
                                                   for bar in all_bars),
        maximum_observed_late_receipt_ns=max(bar['last_late_receipt_after_boundary_ns'] or 0 for bar in all_bars),
        changed_ohlcv_fields_at_boundary=dict(Counter(field for bar in all_bars for field in bar['changed_fields'])),
        recovered_boundary_trades=successor['recovered'],
        fixed_cases=rows,
        frozen_financial_inputs_verified=len(frozen['input_hashes']),
        exchange_kline_publication_verified=False,
        strategy_receipt_verified=False,
        authenticated_account_fill_verified=False,
        financial_accounts=0,
    )


if __name__ == '__main__':
    write(HERE / 'verification.json', audit())
