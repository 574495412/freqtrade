"""Read-only reconstruction from checked monthly archives and existing candles."""
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from freqtrade.data.trendcore_signals import trendcore_weights

HERE=Path(__file__).absolute().parent


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    receipt=json.loads((HERE/'input_receipts.json').read_text());parts={p:[]for p in ['BTC','ETH']}
    gaps=[]
    assert len(receipt['archives'])==48
    for row in receipt['archives']:
        path=Path(row['path']);assert sha(path)==row['sha256']
        checksum=Path(str(path)+'.CHECKSUM');assert sha(checksum)==row['checksum_sha256']
        expected,name=checksum.read_text().strip().split();assert expected==sha(path)and name==path.name
        with ZipFile(path)as z:
            assert z.testzip()is None
            data=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])),header=None)
        dates=pd.to_datetime(data[0],unit='ms',utc=True)
        parts[row['asset']].append(pd.Series(data[4].astype(float).to_numpy(),index=dates))
        if row['missing_hours']:gaps.append(dict(asset=row['asset'],month=row['month'],missing=row['missing_hours']))
    series={}
    for row in receipt['existing_inputs']:
        path=Path(row['local_path']);assert sha(path)==row['local_sha256']
        local=pd.read_feather(path).set_index('date').close
        s=pd.concat([*parts[row['asset']],local]).loc['2023-03-25':'2026-08-31']
        assert not s.index.duplicated().any()
        series[row['asset']+'/USDT']=s
    expected=pd.DataFrame(series);expected.index.name='date'
    actual=pd.read_parquet(receipt['signal_prices']['path'])
    pd.testing.assert_frame_equal(expected,actual,check_freq=False)
    assert sha(receipt['signal_prices']['path'])==receipt['signal_prices']['sha256']
    full=trendcore_weights(actual);cases=[]
    for cutoff in ['2025-01-01T04:00Z','2025-06-30T12:00Z','2026-01-01T05:00Z','2026-08-31T22:00Z']:
        prefix=trendcore_weights(actual.loc[:pd.Timestamp(cutoff)])
        pd.testing.assert_frame_equal(prefix,full.loc[prefix.index],check_freq=False,atol=1e-13,rtol=1e-13)
        assert prefix.index.max()<=pd.Timestamp(cutoff)+pd.Timedelta(hours=1)
        cases.append(cutoff)
    result=dict(passed=True,verified_archives=48,retained_hours_per_asset=len(actual),retained_input_missing_hours=0,excluded_pre_warmup_archive_gaps=gaps,warmup_start=str(actual.index[0]),latest_input_open=str(actual.index[-1]),actual_data_prefix_cases=cases,partial_sixhour_bar_never_admitted=True,source_account_simulator_imported=False,economic_returns_computed=False)
    (HERE/'input_review.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
