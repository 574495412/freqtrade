"""Inspect actual native exits/ZIPs and exact parent economic parity."""
import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_admission_native_20261115_v5/runs'
OLD = ROOT / 'user_data/crypto_quant/copula_fills_native_20261115_v4/runs'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_stats(directory):
    z = next((directory / 'native_results').glob('*.zip'))
    with zipfile.ZipFile(z) as archive:
        name = next(n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json'))
        return next(iter(json.loads(archive.read(name))['strategy'].values()))


def main():
    rows = []
    for process in read(HERE / 'execution_receipt.json'):
        case = process['case']
        directory = OUT / case / 'native2'
        before = OLD / case / 'observed1'
        for path, record in read(directory / 'implementation_freeze.json').items():
            assert sha(record['snapshot']) == record['sha256']
            assert sha(path) == record['sha256']
        if case in ['one_leg_rejected', 'missing_funding_exposed']:
            assert process['returncode'] == 42
            failure = read(directory / 'admission_failure.json')
            assert failure['native_zips'] == [] and not list((directory / 'native_results').glob('*.zip'))
            assert not (directory / 'native_fixture_result.json').exists()
            assert failure['financial_performance'] is None
            fills = sum(len(t['orders']) for t in failure['actual_native_trades'])
            if case == 'one_leg_rejected':
                assert failure['admission']['stage'] == 'paired_phase'
                assert failure['admission']['at'] == '2025-01-02 00:05:00+00:00'
                assert fills == 1 and len(failure['actual_native_trades']) == 1
                assert failure['completed_phase_checks'] == 0
                prior = read(before / 'native_fixture_result.json')['fatal_pair_error']
                assert pd.Timestamp(prior['at']) - pd.Timestamp(failure['admission']['at']) == pd.Timedelta(minutes=5)
            else:
                assert failure['admission']['stage'] == 'missing_funding_while_held'
                assert failure['admission']['at'] == '2025-01-03 11:00:00+00:00'
                gap = failure['gap_checks'][0]
                assert gap['quantity_before_event'] == 4.2 and gap['rate'] is None and gap['exposed']
                partial = pd.read_parquet(directory / 'partial_observations.parquet')
                assert partial.date.max() < pd.Timestamp(gap['at'])
            rows.append(dict(case=case, native_exit=42, aborted_before_export=True,
                stage=failure['admission']['stage'], at=failure['admission']['at'],
                actual_filled_orders_before_abort=fills, financial_performance=None))
            continue
        assert process['returncode'] == 0
        new, old = native_stats(directory), native_stats(before)
        assert new['trades'] == old['trades']
        for key in ['profit_total', 'profit_total_abs', 'final_balance', 'total_trades', 'rejected_signals']:
            assert new[key] == old[key], (case, key)
        actual = pd.read_parquet(directory / 'hourly_equity.parquet')
        expected = pd.read_parquet(before / 'hourly_equity.parquet')
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        result = read(directory / 'native_fixture_result.json')
        assert result['admission_failure'] is None and result['completed_phase_checks'] == 599
        if case == 'missing_funding_flat':
            assert len(result['gap_checks']) == 1
            assert result['gap_checks'][0]['rate'] is None and result['gap_checks'][0]['quantity_before_event'] == 0
        rows.append(dict(case=case, native_exit=0, complete_native_start=True,
            actual_trade_records_exact=True, hourly_nav_all_fields_exact=True,
            hourly_points=len(actual), filled_orders=result['native_orders'],
            completed_phase_checks=result['completed_phase_checks'],
            source20_funded_accounts=0))
    assert len(rows) == 6
    result = dict(passed=True, scenarios=rows, actual_native_attempts=6,
        complete_synthetic_accounts=4, correctly_aborted_accounts=2,
        accepted_fixture_orders=sum(r.get('filled_orders', 0) for r in rows),
        accepted_fixture_hourly_points=sum(r.get('hourly_points', 0) for r in rows),
        native_main_phases_preserved=sum(r.get('completed_phase_checks', 0) for r in rows),
        source20_funded_accounts=0, source_strategy_financial_performance=None)
    (HERE / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print('AUDITED4exact native controls/2early aborts;60control fills,200hourly marks;0source20financial accounts')


if __name__ == '__main__':
    main()
