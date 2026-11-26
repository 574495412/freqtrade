"""Checksummed Binance metrics into native OPEN_INTEREST candle schema."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import time
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/oi_cascade_source_native_20261113_v1'
START = pd.Timestamp('2024-12-28', tz='UTC')
END = pd.Timestamp('2026-09-01', tz='UTC')


def capture(day):
    target = OUT / 'raw' / f'BTCUSDT-metrics-{day}.zip'
    receipt = target.with_suffix('.receipt.json')
    if receipt.exists():
        existing = json.loads(receipt.read_text())
        if sha(target) != existing['sha256']:
            raise ValueError('Changed previous capture')
        return existing
    url = f'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/{target.name}'
    for attempt in range(3):
        response = requests.get(url, proxies={'https': 'http://127.0.0.1:7897'}, timeout=25)
        checksum = requests.get(url + '.CHECKSUM', proxies={'https': 'http://127.0.0.1:7897'}, timeout=25)
        if response.status_code == checksum.status_code == 200:
            break
        if response.status_code == 404 or checksum.status_code == 404:
            save(target.with_suffix('.failure.json'), {'url': url, 'data_status': response.status_code,
                                                      'checksum_status': checksum.status_code})
            raise ValueError('Missing official archive/checksum; do not fabricate')
        time.sleep(attempt + 1)
    response.raise_for_status()
    checksum.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if checksum.text.split()[0] != digest:
        raise ValueError('Official SHA256 mismatch')
    target.write_bytes(response.content)
    target.with_suffix('.CHECKSUM').write_bytes(checksum.content)
    value = {'day': day, 'url': url, 'path': str(target), 'sha256': digest,
             'checksum_sha256': sha(target.with_suffix('.CHECKSUM')),
             'retrieved_at': datetime.now(UTC).isoformat()}
    save(receipt, value)
    return value


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / 'raw').mkdir(exist_ok=True)
    if not (HERE / 'protocol.json').exists():
        save(HERE / 'protocol.json', {
            'registered_at': datetime.now(UTC).isoformat(),
            'source': 'EdgeLuck/freqtrade-open-interest37a8ed8682b79ba29b6bfbc67fff2a696e388826',
            'source_role': 'Author calls it a reference with no demonstrated edge; attribution study, not a proven strategy.',
            'source_formula': '1h close above prior20highs or below prior20lows;5hour base-OI relative increase>0 confirms direction.',
            'source_risk': 'Exact original source10%stop, inert10000%ROI and no discretionary exit; no exit completion invented.',
            'input_contract': 'Same venue Binance archive sum_open_interest, exact top-of-hour raw timestamp. Point is assumed available5min later, therefore before same-row1h-close decision. No value-as-amount, Bybit substitution, missing OI fill or compressed-row shifts.',
            'unknown_policy': 'Explicit full hourly grid with NaN for missing top-of-hour snapshots; disable informative forward-fill. Candidate needs all6consecutive points for5hour change. Two price controls: same availability mask and full price calendar.',
            'universe': ['BTC/USDT:USDT'], 'universe_reason': 'The pinned source explicitly demonstrates BTCUSDT; not deletion of AXS/TRB from a failed source portfolio.',
            'window_utc': ['2025-01-01T00:00:00Z', '2026-08-31T23:00:00Z'],
            'capital_usdt': 1000, 'leverage': 1, 'maximum_open_positions': 1,
            'cost_bps_per_side': [15, 8], 'native_detail': '5m',
            'acceptance': 'Candidate positive at both costs, both continuous calendar slices positive, return above both price controls, hourlyDD<=50%. Even a numeric pass cannot qualify an example with unvalidated exit/independent evidence.',
            'cascade_notebook_rejected': '6f039e64: best_regime selected on same-sample future24h returns; sum of per-event returns is not a funded continuous account. OI only validates a short proxy window; not observed liquidations.',
            'orders_enabled': False, 'production_eligible': False})
    days = [date.strftime('%Y-%m-%d') for date in pd.date_range(START, END, freq='1D', inclusive='left')]
    receipts = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = [pool.submit(capture, day) for day in days]
        for task in as_completed(tasks):
            receipts.append(task.result())
            if len(receipts) % 100 == 0:
                print(json.dumps({'archives_verified': len(receipts), 'required': len(days)}), flush=True)
    receipts.sort(key=lambda row: row['day'])
    save(OUT / 'raw_receipts.json', receipts)
    frames = []
    for receipt in receipts:
        with ZipFile(receipt['path']) as archive:
            if len(archive.namelist()) != 1:
                raise ValueError('One official CSV required')
            frame = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
        if frame.symbol.ne('BTCUSDT').any():
            raise ValueError('Wrong contract')
        frame['date'] = pd.to_datetime(frame.create_time, utc=True)
        if frame.date.dt.strftime('%Y-%m-%d').ne(receipt['day']).any():
            raise ValueError('Wrong archive calendar')
        selected = frame[['date', 'sum_open_interest', 'sum_open_interest_value']].copy()
        for column in selected.columns[1:]:
            selected[column] = pd.to_numeric(selected[column], errors='raise')
        # Keep actually reported zero/NaN values intact for the native schema.
        # A relative-OI signal cannot use them; the strategy's six-positive-point
        # mask blocks those windows. Negative/infinite reports still fail.
        if np.isinf(selected.iloc[:, 1:]).any().any() or selected.iloc[:, 1:].lt(0).any().any():
            raise ValueError('Negative/infinite OI input')
        frames.append(selected)
    raw = pd.concat(frames, ignore_index=True).sort_values('date')
    if raw.date.duplicated().any():
        raise ValueError('Do not drop duplicate source observations')
    hourly = raw.loc[raw.date.dt.minute.eq(0) & raw.date.dt.second.eq(0)].set_index('date')
    clock = pd.date_range(START, END, freq='h', inclusive='left')
    hourly = hourly.reindex(clock).rename_axis('date').reset_index().rename(
        columns={'sum_open_interest': 'open_interest_amount', 'sum_open_interest_value': 'open_interest_value'})
    candles = OUT / 'candles/futures'
    candles.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / 'user_data/crypto_quant/nfi40_data_v002/candles/futures').glob('BTC_USDT_USDT-*.feather'):
        target = candles / source.name
        if not target.exists():
            os.link(source, target)
        elif sha(target) != sha(source):
            raise ValueError('Original frozen candle changed')
    hourly.to_feather(candles / 'BTC_USDT_USDT-1h-open_interest.feather')
    save(OUT / 'data_receipts.json', {str(p.relative_to(OUT / 'candles')): sha(p) for p in sorted(candles.glob('*.feather'))})
    missing = hourly.loc[hourly.open_interest_amount.isna() | hourly.open_interest_amount.le(0), 'date']
    save(HERE / 'input_admission.json', {'passed': True, 'raw_archives': len(receipts),
         'raw_rows': len(raw), 'hourly_rows': len(hourly), 'exact_observed_positive_hours': int(hourly.open_interest_amount.gt(0).sum()),
         'unknown_hour_clocks': [str(x) for x in missing], 'unknown_hours': len(missing),
         'unknowns_not_filled': True, 'amount_column_is_base_units': True,
         'raw_reported_zero_rows_preserved': int(raw.sum_open_interest.eq(0).sum()),
         'availability_is_disclosed_proxy_not_historical_receipt': True,
         'raw_receipts': {'path': str(OUT / 'raw_receipts.json'), 'sha256': sha(OUT / 'raw_receipts.json')},
         'native_oi_file_sha256': sha(candles / 'BTC_USDT_USDT-1h-open_interest.feather')})
    print(json.dumps({'input_admitted': True, 'hours': len(hourly), 'unknown': len(missing)}), flush=True)


if __name__ == '__main__':
    main()
