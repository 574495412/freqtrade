"""Independently reconcile BTC native funding inputs with checksummed public archives."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / 'plan.json').read_text())
    out = Path(plan['output_root']) / 'raw_funding'
    out.mkdir(exist_ok=True)
    months = [x.strftime('%Y-%m') for x in pd.date_range('2025-01-01', '2026-08-01', freq='MS')]

    def capture(month):
        path = out / f'BTCUSDT-fundingRate-{month}.zip'
        url = 'https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/' + path.name
        if not path.exists():
            response = requests.get(url, proxies={'https': 'http://127.0.0.1:7897'}, timeout=25)
            checksum = requests.get(url + '.CHECKSUM', proxies={'https': 'http://127.0.0.1:7897'}, timeout=25)
            response.raise_for_status()
            checksum.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != checksum.text.split()[0]:
                raise ValueError('Funding checksum mismatch')
            path.write_bytes(response.content)
            path.with_suffix('.CHECKSUM').write_bytes(checksum.content)
        if sha(path) != path.with_suffix('.CHECKSUM').read_text().split()[0]:
            raise ValueError('Changed funding capture')
        with ZipFile(path) as archive:
            frame = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
        return frame, {'month': month, 'url': url, 'path': str(path), 'sha256': sha(path),
                       'checksum_sha256': sha(path.with_suffix('.CHECKSUM')),
                       'retrieved_at': datetime.now(UTC).isoformat()}

    with ThreadPoolExecutor(max_workers=6) as pool:
        captured = list(pool.map(capture, months))
    frame = pd.concat([row[0] for row in captured], ignore_index=True)
    dates = pd.to_datetime(frame.calc_time, unit='ms', utc=True)
    frame['date'] = dates.dt.floor('h')
    if frame.date.duplicated().any() or (dates-frame.date).dt.total_seconds().gt(1).any():
        raise ValueError('Ambiguous/native-incompatible funding model clock')
    expected = pd.date_range(plan['start'], plan['end'], freq='8h', inclusive='left').as_unit('ns')
    if not pd.DatetimeIndex(frame.date).as_unit('ns').equals(expected) or frame.funding_interval_hours.ne(8).any():
        raise ValueError('BTC funding schedule is incomplete/changed; retain UNKNOWN')
    native = pd.read_feather(Path(plan['datadir']) / 'futures/BTC_USDT_USDT-1h-funding_rate.feather')
    native = native.loc[native.date.ge(expected[0]) & native.date.le(expected[-1])]
    if not pd.DatetimeIndex(native.date).as_unit('ns').equals(expected):
        raise ValueError('Native BTC funding lacks an archive event')
    if not np.allclose(native.funding_rate, frame.last_funding_rate, atol=1e-12, rtol=0):
        raise ValueError('Native and independent archived rates differ')
    save(HERE / 'funding_audit.json', {'passed': True, 'archives': [row[1] for row in captured],
         'full_window_events': len(frame), 'eight_hour_calendar_complete': True,
         'native_rates_match': True, 'unknown_held_slots_from_prior_AXS_TRB_study_apply': False,
         'clock_is_native_hour_bucket_not_authenticated_account_order': True,
         'actual_account_funding_payments_certified': False})
    print(json.dumps({'funding_inputs_passed': True, 'complete_events': len(frame)}))


if __name__ == '__main__':
    main()
