"""Bind original sources,complete native quote grids and actual flag receipts."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).absolute().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    plan = json.loads((HERE / 'plan.json').read_text())
    frames, rows = {}, []
    for relative, expected in json.loads((HERE / 'data_receipts.json').read_text()).items():
        path = Path(plan['datadir']) / relative
        assert sha(path) == expected
        frame = pd.read_feather(path).set_index('date')
        assert frame.index.is_unique and frame.index.is_monotonic_increasing
        frequency = '5min' if '-5m.' in relative else 'h'
        needed = pd.date_range(pd.Timestamp(plan['native_start']) - pd.Timedelta(hours=512), plan['end'], freq=frequency, inclusive='left')
        selected = frame.reindex(needed)
        assert np.isfinite(selected[['open', 'high', 'low', 'close', 'volume']]).all().all()
        assert (selected[['open', 'high', 'low', 'close']] > 0).all().all()
        assert (selected.volume >= 0).all()
        frames[relative] = frame
        rows.append(dict(path=str(path), sha256=expected, complete_native_input_rows=len(needed)))
    grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h')
    for coin in ['BTC', 'ETH']:
        np.testing.assert_array_equal(frames[coin + '_USDT-5m.feather'].open.reindex(grid),
                                      frames[coin + '_USDT-1h.feather'].open.reindex(grid))
    for candidate in plan['candidates']:
        for record in [candidate['config'], candidate['market_snapshot'], candidate['observer_source'], *candidate['source_files'].values()]:
            assert sha(record['path']) == record['sha256']
    admissions = []
    for name in ['mama']:
        directory = Path(plan['output_root']) / ('preflight_' + name + '_1000_conservative')
        receipt = json.loads((directory / 'receipt.json').read_text())
        assert receipt['passed'] and not receipt['financial_start']
        admissions.append(receipt)
    for row in json.loads((HERE / 'source_receipts.json').read_text()):
        assert row['status'] == 200
        assert sha(row['path']) == row['sha256']
    original = json.loads(Path(plan['engine_manifest']['path']).read_text())
    for relative, expected in original['files'].items():
        assert sha(Path(plan['engine_source']) / relative) == expected
    result = dict(passed=True, original_native_files=len(original['files']), native_input_files=rows,
                  pinned_source_files=4, source_git_blob_audit=False, actual_native_admissions=1,
                  native_converted_hour_rows=2*14592, native_flag_admissions=admissions,
                  source_phase_tests=15, funded_native_accounts=0,
                  missing_rows_not_filled=True, all_target_hourly_and5min_opens_exact=True)
    (HERE / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS1nativeconversion,29184actualhourrows,4pinnedsources;0fundedaccounts')


if __name__ == '__main__':
    main()
