"""Compare pinned author daily OI to already captured primary raw observations."""
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


def main():
    source=HERE/'source/data/intraday/BTCUSDT_oi_daily.parquet'
    author=pd.read_parquet(source).iloc[:,0];author.index=author.index.tz_localize('UTC')
    receipt_path=ROOT/'user_data/crypto_quant/oi_cascade_source_native_20261113_v1/raw_receipts.json'
    receipts=json.loads(receipt_path.read_text());rows=[];exact=[]
    for r in receipts:
        path=Path(r['path']);assert sha(path)==r['sha256']
        checksum=path.with_suffix('.CHECKSUM');assert sha(checksum)==r['checksum_sha256']
        assert checksum.read_text().split()[0]==r['sha256']
        with ZipFile(path)as z:
            assert z.testzip()is None
            frame=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        times=pd.to_datetime(frame.create_time,utc=True);day=pd.Timestamp(r['day'],tz='UTC')
        assert times.dt.floor('D').eq(day).all()and frame.symbol.eq('BTCUSDT').all()
        values=frame.sum_open_interest_value.astype(float);a=float(author.loc[day]);errors=np.abs(values.to_numpy()-a)
        nearest=int(np.argmin(errors));matches=times.loc[errors<=max(1e-5,abs(a)*1e-12)]
        rows.append(dict(day=str(day),author=a,raw_rows=len(frame),raw_first=float(values.iloc[0]),raw_last=float(values.iloc[-1]),raw_mean=float(values.mean()),nearest_sample_error=float(errors[nearest]),nearest_sample_time=str(times.iloc[nearest]),exact_sample_times=matches.astype(str).tolist()))
        exact.extend(matches.astype(str).tolist())
    result=dict(source_parquet_sha256=sha(source),raw_receipts=dict(path=str(receipt_path),sha256=sha(receipt_path)),days_compared=len(rows),author_timezone_interpretation='naivecalendarassumedUTCforcomparison,notobservedpublicationtime',raw_daily_first_matches=sum(abs(r['author']-r['raw_first'])<=max(1e-5,r['author']*1e-12)for r in rows),raw_daily_last_matches=sum(abs(r['author']-r['raw_last'])<=max(1e-5,r['author']*1e-12)for r in rows),raw_daily_mean_matches=sum(abs(r['author']-r['raw_mean'])<=max(1e-5,r['author']*1e-12)for r in rows),days_with_any_exact_sample=sum(bool(r['exact_sample_times'])for r in rows),rows=rows,economic_results_computed=False)
    (HERE/'daily_source_probe.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k!='rows'}))
    print('FIRST3',json.dumps(rows[:3]))


if __name__=='__main__':main()
