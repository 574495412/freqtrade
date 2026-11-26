"""Read exact Southbound calendar cells; run with the bundled PDF runtime."""
import hashlib
import json
import csv
from pathlib import Path

import pdfplumber

HERE = Path(__file__).resolve().parent
MONTHS = dict(zip(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], range(1, 13)))


def main():
    receipts = json.loads((HERE / 'calendar_source_receipts.json').read_text())
    extracted = {}
    for row in receipts:
        path = Path(row['path'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        year = int(path.stem[-4:])
        with pdfplumber.open(path) as pdf:
            # Relevant pages were rendered and visually reviewed. 2026 Jul/Aug
            # have no absent source dates; only page 1 is needed for these cells.
            pages = pdf.pages if year == 2025 else pdf.pages[:1]
            for number, page in enumerate(pages, 1):
                words = page.extract_words()
                for header in [w for w in words if w['text'] in MONTHS and w['x0'] < 140]:
                    month = MONTHS[header['text']]
                    days = [w for w in words if w['text'].isdigit() and 1 <= int(w['text']) <= 31 and abs(w['top'] - header['top']) < 2]
                    assert days
                    south = min((w for w in words if w['text'] == 'Southbound' and w['top'] > header['top']), key=lambda w: w['top'])
                    closed = [w for w in words if w['text'] == 'Closed' and abs(w['top'] - south['top']) < 2]
                    for cell in closed:
                        center = (cell['x0'] + cell['x1']) / 2
                        day = min(days, key=lambda w: abs((w['x0'] + w['x1']) / 2 - center))
                        assert abs((day['x0'] + day['x1']) / 2 - center) < 5
                        date = f'{year}-{month:02}-{int(day["text"]):02}'
                        extracted[date] = dict(date=date, status='Closed', row='Southbound Trading',
                            page=number, path=str(path), source_sha256=row['sha256'],
                            bbox=[cell['x0'], cell['top'], cell['x1'], cell['bottom']])
    readiness = json.loads((HERE / 'hk_flow_input_readiness.json').read_text())
    # Reconstruct every absence directly with the standard CSV reader, without
    # importing the preceding pandas readiness calculator or its date list.
    original = json.loads((HERE.parent / 'high_recency_native_20261114_v1/inputs.json').read_text())
    schedule = Path(original['schedules']['XHKG']['path'])
    assert hashlib.sha256(schedule.read_bytes()).hexdigest() == original['schedules']['XHKG']['sha256']
    with schedule.open() as stream:
        reader = csv.reader(stream)
        next(reader)
        expected = {r[0][:10] for r in reader if '2025-01-01' <= r[0][:10] <= '2026-08-31'}
    assert len(expected) == 408
    observed = {r['symbol']: set() for r in readiness['records']}
    with (HERE / 'sources/fortune_data_southbound_holdings_southbound_long.csv').open() as stream:
        for row in csv.DictReader(stream):
            if row['pool_code'] in observed:
                observed[row['pool_code']].add(row['date'])
    ordinary = [symbol for symbol in observed if symbol != '2800.HK']
    assert len(ordinary) == 10 and not observed['2800.HK']
    missing = sorted(expected - observed[ordinary[0]])
    assert all(expected - observed[symbol] == set(missing) for symbol in ordinary)
    cells = [extracted[day] for day in missing]
    assert len(cells) == 15
    assert sum(r['window_unique_dates'] == 393 for r in readiness['records']) == 10
    result = dict(passed=True, verified_missing_dates=cells,
        conclusion='All 15 common absent HK trading dates are official Southbound Closed dates, not established missing open-channel observations',
        source_open_channel_dates_covered_per_ordinary_stock=393,
        ordinary_stocks=10, original_etf='2800.HK absent in source; not changed to zero holdings',
        publication_clock_certified=False, admitted_for_prediction=False,
        no_imputation=True, no_research_pool_or_window_change=True,
        initial_readiness_sha256=hashlib.sha256((HERE / 'hk_flow_input_readiness.json').read_bytes()).hexdigest())
    (HERE / 'connect_calendar_reconciliation.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASSED 15 official Southbound Closed cells; no observed open-channel gaps for 10 original ordinary stocks; 2800 and first-seen remain unsupported')


if __name__ == '__main__':
    main()
