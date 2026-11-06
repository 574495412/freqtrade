"""Check registered public Binance archives against historical execution inputs."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'rd_program_20261105' / 'data_extension'))
from probe_event_sources import decode, fetch, now, sha, write, CAP, EXPANDED

PLAN = HERE / 'registration.json'
OUT = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1/public_depth_window_probe_v001'
SAMPLES = [('L', 'BTCUSDT', '2026-08-31'), ('S', 'BTCUSDT', '2026-10-23'), ('S', 'SOLUSDT', '2026-10-23')]


def inspect_archive(data, kind):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].file_size > EXPANDED:
            raise ValueError('Unexpected archive members or expansion size')
        with archive.open(members[0]) as stream:
            rows = csv.DictReader(io.TextIOWrapper(stream))
            columns = rows.fieldnames
            if kind == 'bookTicker':
                return dict(columns=columns, rows=sum(1 for _ in rows))
            if columns != ['timestamp', 'percentage', 'depth', 'notional']:
                raise ValueError('Unexpected depth archive columns')
            bands = defaultdict(list)
            for row in rows:
                at = datetime.fromisoformat(row['timestamp'])
                band, depth, notional = [Decimal(row[k]) for k in ('percentage', 'depth', 'notional')]
                if any(not v.is_finite() for v in (band, depth, notional)) or min(depth, notional) < 0:
                    raise ValueError('Non-finite or negative depth values')
                bands[band].append((at, depth, notional))
    summary = {}
    for band, values in sorted(bands.items()):
        seen = set()
        run = longest = 0
        previous = None
        for at, depth, notional in values:
            pair = (depth, notional)
            run = run+1 if pair == previous else 1
            longest = max(longest, run)
            previous = pair
            seen.add(at)
        summary[str(band.normalize())] = dict(rows=len(values), duplicate_timestamps=len(values)-len(seen),
            first_archive_timestamp=str(values[0][0]), last_archive_timestamp=str(values[-1][0]),
            monotonic=all(a[0] <= b[0] for a,b in zip(values,values[1:])),
            distinct_depth_notional=len({v[1:] for v in values}), longest_unchanged_run=longest)
    return dict(columns=columns, bands=summary, rows=sum(len(v) for v in bands.values()),
        raw_point_two_percent_both_sides=all(b in bands for b in (Decimal('-.2'), Decimal('.2'))))


def read_response(url, path):
    started = now()
    status, headers, body, state = fetch(url)
    info = dict(url=url, http_status=status, headers=headers, state=state,
        started_at_utc=started, finished_at_utc=now())
    if body is not None:
        with path.open('xb') as handle:
            handle.write(body)
        info.update(path=str(path.relative_to(ROOT)), bytes=len(body), sha256=sha(body))
    decoded = decode(body, headers) if status == 200 and state == 'body_complete' else None
    return info, decoded


def audit(item):
    result = dict(item)
    name = f"{item['symbol']}-{item['kind']}-{item['day']}.zip"
    try:
        result['archive'], data = read_response(item['url'], OUT/name)
        if data is None:
            return result
        result['checksum'], checksum = read_response(item['url']+'.CHECKSUM', OUT/(name+'.CHECKSUM'))
        if checksum is None or sha(data) != checksum.decode().split()[0]:
            raise ValueError('Archive lacks a matching publisher checksum')
        result['publisher_checksum_verified'] = True
        result['schema'] = inspect_archive(data, item['kind'])
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    return result


if __name__ == '__main__':
    source_hash = sha(Path(__file__).read_bytes())
    if sys.argv[1:] == ['register']:
        samples = [dict(window=window, symbol=symbol, day=day, kind=kind,
            url=f'https://data.binance.vision/data/futures/um/daily/{kind}/{symbol}/{symbol}-{kind}-{day}.zip')
            for window,symbol,day in SAMPLES for kind in ('bookDepth', 'bookTicker')]
        write(PLAN, dict(registered_at_utc=now(), source_sha256=source_hash, samples=samples,
            maximum_requests=12, workers=3, timeout_seconds=30, max_raw_bytes=CAP,
            max_expanded_bytes=EXPANDED, retries=0, financial_accounts=0))
    elif sys.argv[1:] == ['run']:
        plan = json.loads(PLAN.read_text())
        if plan['source_sha256'] != source_hash:
            raise SystemExit('Registered source hash differs')
        OUT.mkdir(parents=True, exist_ok=False)
        with ThreadPoolExecutor(max_workers=plan['workers']) as workers:
            result = list(workers.map(audit, plan['samples']))
        write(HERE/'result.json', dict(registration_sha256=sha(PLAN.read_bytes()), results=result,
            economic_admission=False, new_financial_accounts=0))
    else:
        raise SystemExit('Use register or run')
