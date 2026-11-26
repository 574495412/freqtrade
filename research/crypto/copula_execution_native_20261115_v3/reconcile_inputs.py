"""Independent raw archive, native frame and public REST input reconciliation."""
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/copula_execution_native_20261115_v3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    a = json.loads((HERE / 'input_admission.json').read_text())
    for key in ['data_receipts', 'funding_events']:
        assert sha(a[key]['path']) == a[key]['sha256']
    receipts = json.loads(Path(a['data_receipts']['path']).read_text())
    assert len(receipts) == 80
    for relative, expected in receipts.items():
        assert sha(OUT / 'candles' / relative) == expected
    raw_funding, repairs, monthly = [], [], 0
    for row in a['raw_receipts']:
        assert row['passed']
        for key in ['archive', 'checksum', 'prepared']:
            assert sha(row[key]['path']) == row[key]['sha256']
        assert Path(row['checksum']['path']).read_text().split()[0] == row['archive']['sha256']
        monthly += 1
        for repair in row.get('daily_repairs', []):
            for key in ['archive', 'checksum']:
                assert sha(repair[key]['path']) == repair[key]['sha256']
            assert Path(repair['checksum']['path']).read_text().split()[0] == repair['archive']['sha256']
            assert repair['rows_inserted'] == 24
            repairs.append(repair)
        if row['kind'] != 'funding':
            continue
        with zipfile.ZipFile(row['archive']['path']) as archive:
            frame = pd.read_csv(archive.open(archive.namelist()[0]))
        frame['symbol'] = row['symbol']
        frame['event_time'] = pd.to_datetime(frame.calc_time, unit='ms', utc=True)
        frame['date'] = frame.event_time.dt.floor('h')
        if row['symbol'] == 'EOS':
            frame = frame.loc[frame.event_time < pd.Timestamp('2025-05-21T09:00Z')]
        raw_funding.append(frame)
    raw = pd.concat(raw_funding, ignore_index=True).sort_values(['symbol', 'event_time']).reset_index(drop=True)
    events = pd.read_parquet(a['funding_events']['path']).sort_values(['symbol', 'event_time']).reset_index(drop=True)
    columns = ['symbol', 'event_time', 'date', 'funding_interval_hours', 'last_funding_rate']
    pd.testing.assert_frame_equal(raw[columns], events[columns], check_exact=True)
    assert len(events) == 39412 and len(repairs) == 6 and monthly == 670
    # Native loader rows match actual observed public rates; no synthetic zero events.
    for symbol in a['source20']:
        f = pd.read_feather(OUT / f'candles/futures/{symbol}_USDT_USDT-1h-funding_rate.feather')
        expected = events.loc[events.symbol.eq(symbol)]
        np.testing.assert_array_equal(pd.DatetimeIndex(f.date), pd.DatetimeIndex(expected.date))
        np.testing.assert_array_equal(f.funding_rate, expected.last_funding_rate)
    api = json.loads((HERE / 'funding_rest_receipts.json').read_text())
    comparisons, differences = [], []
    for rec in api['requests']:
        assert rec['status'] == 'fulfilled' and not rec['value']['response'].get('isError')
        req, response = rec['value']['request'], rec['value']['response']['structuredContent']['result']
        matches = events.loc[events.symbol.eq(req['symbol'].removesuffix('USDT')) & events.calc_time.between(req['startTime'], req['endTime'])]
        assert [int(r['fundingTime']) for r in response] == matches.calc_time.tolist()
        err = np.abs(np.array([float(r['fundingRate']) for r in response]) - matches.last_funding_rate.to_numpy())
        assert (err < 1e-15).all()
        differences.extend(err.tolist())
        comparisons.append(dict(request=req, matched_rows=len(matches), missing_records_not_filled=True))
    forecasts = pd.read_parquet(ROOT / 'user_data/crypto_quant/copula_account_native_20261115_v2/native_hourly_forecasts.parquet')
    issues = []
    for issue in a['issues']:
        s = issue['symbol']
        t = pd.Timestamp(issue['event_time']).floor('h')
        seq = events.loc[events.symbol.eq(s)].reset_index(drop=True)
        at = seq.index[seq.date.eq(t)][0]
        previous, current = seq.iloc[at - 1], seq.iloc[at]
        transition = int(previous.funding_interval_hours) != int(current.funding_interval_hours)
        if transition:
            # A new aligned grid can have a shortened first interval; not proof
            # of the exact historical announcement/adjustment knowledge clock.
            step = int(current.funding_interval_hours)
            next_boundary = previous.date.floor(f'{step}h') + pd.Timedelta(hours=step)
            assert next_boundary == current.date
            unknown = previous.date + pd.Timedelta(hours=int(previous.funding_interval_hours))
        else:
            unknown = previous.date + pd.Timedelta(hours=int(current.funding_interval_hours))
        context = forecasts.loc[unknown - pd.Timedelta(hours=1):unknown + pd.Timedelta(hours=1)]
        possible = {}
        for alpha in [10, 15, 20]:
            selected = context.symbol1.eq(s) | context.symbol2.eq(s)
            possible[str(alpha)] = bool((selected & context[f'state_{alpha}'].ne(0)).any())
        assert not any(possible.values())
        issues.append(dict(original_issue=issue, previous_event=str(previous.event_time), previous_interval=int(previous.funding_interval_hours),
            current_interval=int(current.funding_interval_hours), classification='compatible_UTC_grid_interval_transition' if transition else 'unresolved_public_event_gap',
            absent_old_grid_hour=str(unknown), zero_rate_assumed=False, nominal_forecast_exposure=possible,
            actual_native_held_exposure_verified=False))
    result = dict(passed_within_raw_native_and_public_api_scope=True, monthly_archives_verified=monthly,
        native_files_verified=80, daily_mark_archives_verified=len(repairs), actual_daily_mark_rows_inserted=sum(r['rows_inserted'] for r in repairs),
        public_funding_rows=len(events), funding_rate_maximum_rest_error=max(differences), public_rest_comparisons=comparisons,
        cadence_issue_classifications=issues, original_admission_unchanged=True, no_missing_funding_rates_filled=True,
        funded_admission=False, actual_native_funding_exposure_audit_required=True, native_funded_accounts=0,
        original_input_admission_sha256=sha(HERE / 'input_admission.json'))
    (HERE / 'input_reconciliation.json').write_text(json.dumps(result, indent=2) + '\n')
    print('RECONCILED670monthly+6daily archives,80nativefiles,39412public events;4issues retained;0accounts')


if __name__ == '__main__':
    main()
