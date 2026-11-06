"""Validate registered public messages, retaining prospectivity as an evidence boundary."""
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path

from capture_public import HERE, ROOT, PLAN, RESULT, digest, write
from final_kline import MS, parse_final

FROZEN = HERE.parent / 'factor_cost_futures_long_20261106/plan.json'


def serial(row):
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(row).items()}


def audit():
    plan = json.loads(PLAN.read_text())
    capture = json.loads(RESULT.read_text())
    if capture['registration_sha256'] != digest(PLAN):
        raise ValueError('Capture registration mismatch')
    if [row['market'] for row in capture['captures']] != list(plan['markets']):
        raise ValueError('Capture market set changed')
    reports = []
    for row in capture['captures']:
        market = row['market']
        result = dict(market=market, status=row['status'], errors=[], finals=[])
        if row['url'] != plan['markets'][market]:
            raise ValueError('Unexpected captured stream')
        if 'path' not in row:
            reports.append(result)
            continue
        path = ROOT / row['path']
        if digest(path) != row['sha256']:
            raise ValueError('Raw WebSocket capture changed')
        previous = row['connected_ns']
        counts = Counter()
        with path.open() as handle:
            for line, raw in enumerate(handle, 1):
                frame = json.loads(raw)
                received = frame['collector_received_ns']
                counts['raw_frames'] += 1
                if not previous <= received <= row['finished_ns']:
                    raise ValueError('Nonmonotonic local receive clock')
                previous = received
                try:
                    final = parse_final(frame['raw'], received, market)
                except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
                    counts['rejected_frames'] += 1
                    result['errors'].append(dict(line=line, reason=str(exc)))
                    continue
                if final is None:
                    counts['provisional_frames'] += 1
                    continue
                counts['final_frames'] += 1
                if final.end_ms * MS == plan['target_boundary_ns']:
                    counts['target_boundary_final_frames'] += 1
                result['finals'].append(dict(serial(final), line=line,
                    exchange_publish_after_boundary_ms=final.publish_ms-final.end_ms,
                    collector_receive_after_boundary_ns=final.received_ns-final.end_ms*MS))
        if counts['raw_frames'] != row['received_frames']:
            raise ValueError('Stored frame count changed')
        result['counts'] = dict(counts)
        reports.append(result)
    frozen = json.loads(FROZEN.read_text())['input_hashes']
    for path, expected in frozen.items():
        if digest(ROOT / path) != expected:
            raise ValueError('Frozen input changed: ' + path)
    return dict(
        capture_registration_sha256=digest(PLAN), capture_receipts_sha256=digest(RESULT),
        normalization_source_sha256=digest(HERE / 'final_kline.py'),
        frozen_feature_source_sha256=digest(HERE.parent / 'rd_program_20261105/factors/features.py'),
        source_sha256=digest(Path(__file__)), market_reports=reports,
        frozen_financial_inputs_verified=len(frozen),
        evidence_class='prospective_public_collector_only',
        historical_kline_publication_verified=False,
        historical_strategy_receipt_verified=False,
        full_RD1_financial_admission=False, financial_accounts=0,
    )


if __name__ == '__main__':
    write(HERE / 'verification.json', audit())
