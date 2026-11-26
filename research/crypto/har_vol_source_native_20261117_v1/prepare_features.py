"""Source-exact feature preparation from checksummed spot minute archives."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent
ROOT=HERE.parents[2]
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_base','taker_buy_quote','ignore']
KEEP=['open','high','low','close','volume','quote_volume','trades','taker_buy_base']


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str,allow_nan=False)+'\n')


def main():
    assert json.loads((HERE/'capture_result.json').read_text())['passed']
    receipts=json.loads((HERE/'raw_receipts.json').read_text());assert len(receipts)==94
    spec=importlib.util.spec_from_file_location('pinned_har_features',HERE/'source/src/ivr/features.py');features=importlib.util.module_from_spec(spec);spec.loader.exec_module(features)
    output=ROOT/'user_data/crypto_quant'/HERE.name;output.mkdir(exist_ok=True)
    parent=HERE.with_name('trendcore_source_native_20261117_v1');old=json.loads((parent/'plan.json').read_text())
    records=[];checks=[]
    for symbol in ['BTCUSDT','ETHUSDT']:
        parts=[];raw_rows=missing_minutes=0
        for r in [r for r in receipts if r['symbol']==symbol]:
            path=Path(r['path']);assert sha(path)==r['sha256'];checksum=path.with_suffix('.CHECKSUM');assert sha(checksum)==r['checksum_sha256']
            expected,name=checksum.read_text().strip().split();assert expected==r['sha256']and name==path.name
            with ZipFile(path)as z:
                assert z.testzip()is None and len(z.namelist())==1
                raw=z.read(z.namelist()[0]);header=0 if raw[:1].isalpha()else None
                frame=pd.read_csv(io.BytesIO(raw),header=header,names=COLS)
            t=frame.open_time.astype('int64');unit='us'if t.iloc[0]>1e14 else'ms';ix=pd.DatetimeIndex(pd.to_datetime(t,unit=unit,utc=True)).tz_localize(None);ix.name='ts'
            month=pd.Timestamp(r['month']+'-01');grid=pd.date_range(month,month+pd.offsets.MonthBegin(1),freq='min',inclusive='left')
            assert ix.is_monotonic_increasing and not ix.duplicated().any()and len(ix.difference(grid))==0
            missing_minutes+=len(grid.difference(ix));raw_rows+=len(frame)
            d=frame[KEEP].astype('float64');d.index=ix
            assert np.isfinite(d.to_numpy()).all()and d[['open','high','low','close']].gt(0).all().all()
            parts.append(d)
        minute=pd.concat(parts);assert not minute.index.duplicated().any()
        assert minute.index[0]==pd.Timestamp('2022-10-01')and minute.index[-1]==pd.Timestamp('2026-08-31T23:59')
        daily=features.daily_features(minute);path=output/(symbol+'_daily_features.parquet');daily.to_parquet(path)
        # Causal features only: forward labels intentionally change with truncation.
        feature_cols=[c for c in daily if not c.startswith('target_')]
        for cutoff in ['2024-12-31','2025-07-31','2026-01-31']:
            end=pd.Timestamp(cutoff)+pd.Timedelta(days=1);prefix=features.daily_features(minute.loc[minute.index<end])
            pd.testing.assert_frame_equal(prefix[feature_cols],daily.loc[:cutoff,feature_cols],atol=1e-12,rtol=1e-10,check_freq=False)
            checks.append(dict(symbol=symbol,cutoff=cutoff,passed=True))
        # Independently recover native hourly OPEN marks from raw first observations.
        native=pd.read_feather(Path(old['datadir'])/(symbol.replace('USDT','_USDT')+'-1h.feather')).set_index('date').open
        grid=pd.date_range(old['start'],old['comparison_end'],freq='h').tz_localize(None)
        actual=minute.open.reindex(grid);reference=native.reindex(grid.tz_localize('UTC'))
        error=float(np.max(np.abs(actual.to_numpy()-reference.to_numpy())));assert np.isfinite(actual).all()and error<1e-8
        records.append(dict(symbol=symbol,path=str(path),sha256=sha(path),rows=len(daily),raw_rows=raw_rows,missing_minutes=missing_minutes,dropped_or_unknown_days=daily.index[daily.rv.isna()].astype(str).tolist(),max_native_hourly_open_error=error,empty5min_ffill_is_source_model_not_observation=True))
        print('FEATURES',symbol,raw_rows,'minuteobservations',len(daily),'days',flush=True)
        del parts,minute,daily
    (output/'candles').mkdir(exist_ok=True);links=[]
    data=json.loads((parent/'data_receipts.json').read_text())
    for name,digest in data.items():
        src=Path(old['datadir'])/name;dst=output/'candles'/name;assert sha(src)==digest
        if not dst.exists():dst.hardlink_to(src)
        assert sha(dst)==digest and src.stat().st_ino==dst.stat().st_ino;links.append(dict(source=str(src),target=str(dst),sha256=digest))
    save(HERE/'data_receipts.json',data)
    save(HERE/'feature_receipts.json',dict(passed=True,records=records,source_feature_code_sha256=sha(HERE/'source/src/ivr/features.py'),raw_receipts_sha256=sha(HERE/'raw_receipts.json'),actual_prefix_checks=checks,hardlinks=links,new_financial_accounts=0,source_account_simulator_executed=False))


if __name__=='__main__':main()
