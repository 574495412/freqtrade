"""Admit source daily OI only through exact primary observations; no accounts."""
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')


def main():
    assert json.loads((HERE/'capture_result.json').read_text())['passed']
    cached=ROOT/'user_data/crypto_quant/oi_cascade_source_native_20261113_v1/raw_receipts.json'
    receipts=json.loads((HERE/'warmup_receipts.json').read_text())+json.loads(cached.read_text())
    receipts.sort(key=lambda r:r['day']);expected=pd.date_range('2024-01-01','2026-08-31',freq='D',tz='UTC')
    assert len(receipts)==974 and [r['day']for r in receipts]==[str(d.date())for d in expected]
    author=pd.read_parquet(HERE/'source/data/intraday/BTCUSDT_oi_daily.parquet').iloc[:,0];author.index=author.index.tz_localize('UTC')
    rows=[];unsorted=[];zero=0;missing=0;max_relative=0.
    for r in receipts:
        path=Path(r['path']);assert sha(path)==r['sha256']
        checksum=path.with_suffix('.CHECKSUM');assert sha(checksum)==r['checksum_sha256']and checksum.read_text().split()[0]==r['sha256']
        with ZipFile(path)as z:
            assert z.testzip()is None and len(z.namelist())==1
            raw=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        times=pd.to_datetime(raw.create_time,utc=True);day=pd.Timestamp(r['day'],tz='UTC')
        assert times.dt.floor('D').eq(day).all()and raw.symbol.eq('BTCUSDT').all()
        if not times.is_monotonic_increasing:unsorted.append(r['day'])
        missing+=len(pd.date_range(day,day+pd.Timedelta(days=1),freq='5min',inclusive='left').difference(times))
        zero+=int(raw.sum_open_interest_value.le(0).sum())
        observed=day+pd.Timedelta(hours=23,minutes=55);chosen=raw.loc[times.eq(observed)]
        assert len(chosen)==1,('Missing/duplicate exactdailyclose',r['day'])
        value=float(chosen.sum_open_interest_value.iloc[0]);assert np.isfinite(value)and value>0
        a=float(author.loc[day]);delta=abs(a-value);assert delta<=max(1e-5,abs(a)*1e-12),(day,a,value)
        max_relative=max(max_relative,delta/abs(a))
        rows.append(dict(day=day,sum_open_interest_value=value,observed_at=observed,availability_proxy=day+pd.Timedelta(days=1),source_sha256=r['sha256']))
    daily=pd.DataFrame(rows).set_index('day')
    output=ROOT/'user_data/crypto_quant'/HERE.name;output.mkdir(exist_ok=True)
    daily.to_parquet(output/'oi_daily.parquet')
    source=HERE.with_name('trendcore_source_native_20261117_v1');parent=json.loads((source/'plan.json').read_text())
    (output/'candles').mkdir(exist_ok=True)
    links=[]
    for name,digest in json.loads((source/'data_receipts.json').read_text()).items():
        src=Path(parent['datadir'])/name;dst=output/'candles'/name
        assert sha(src)==digest
        if not dst.exists():dst.hardlink_to(src)
        assert sha(dst)==digest and src.stat().st_ino==dst.stat().st_ino
        links.append(dict(path=str(dst),source=str(src),sha256=digest))
    save(HERE/'raw_receipts.json',receipts)
    save(HERE/'data_receipts.json',json.loads((source/'data_receipts.json').read_text()))
    save(HERE/'input_admission.json',dict(passed=True,daily_rows=len(daily),source_days_matched=974,missing_daily_close=0,unsorted_archives=len(unsorted),unsorted_days=unsorted,raw_zero_value_rows_preserved=zero,raw_missing_5minute_timestamps=missing,daily_close_is_exact_observed_not_imputed=True,maximum_source_relative_error=max_relative,availability_is_proxy_not_historical_publication_receipt=True,oi_daily=dict(path=str(output/'oi_daily.parquet'),sha256=sha(output/'oi_daily.parquet')),raw_receipts=dict(path=str(HERE/'raw_receipts.json'),sha256=sha(HERE/'raw_receipts.json')),execution_hardlinks=links,new_native_wallets=0))
    print('ADMITTED974dailyclosepoints;authorparquetmatchesprimarysource;unsorted',len(unsorted),'0accounts')


if __name__=='__main__':main()
