"""Official minute-volume observations; no strategy or account simulation."""
import hashlib
import json
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/signed_jump_native_20261114_v1'
SYMBOLS=['ADA','BNB','BTC','DOGE','ETH','LINK','LTC','XRP']
MONTHS=pd.date_range('2024-12-01','2026-08-01',freq='MS').strftime('%Y-%m').tolist()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')

def task(sym,month):
    if shutil.disk_usage(ROOT).free<2*1024**3:raise RuntimeError('Need2GiBfree for input preparation')
    name=f'{sym}USDT-1m-{month}.zip';path=OUT/'archives'/name
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}USDT/1m/{name}'
    for p,u in [(path,url),(path.with_suffix('.zip.CHECKSUM'),url+'.CHECKSUM')]:
        if not p.exists():
            response=requests.get(u,timeout=40)
            if response.status_code!=200:
                failure=OUT/'archives'/(p.name+'.failed_response');failure.write_bytes(response.content)
                raise RuntimeError(f'{u}:HTTP{response.status_code}')
            p.write_bytes(response.content)
    check=path.with_suffix('.zip.CHECKSUM');assert check.read_text().split()[0]==sha(path),path
    frame=pd.read_csv(zipfile.ZipFile(path).open(name[:-4]+'.csv'))
    assert {'open_time','open','high','low','close','volume','quote_volume'}<=set(frame),frame.columns
    time=pd.DatetimeIndex(pd.to_datetime(frame.open_time,unit='ms',utc=True))
    start=pd.Timestamp(month+'-01',tz='UTC');end=start+pd.offsets.MonthBegin(1)
    assert time.equals(pd.date_range(start,end,freq='min',inclusive='left')),name
    values=frame[['open','high','low','close','volume','quote_volume']].to_numpy(dtype=float)
    assert np.isfinite(values).all() and (values[:,:4]>0).all() and (values[:,4:]>=0).all(),name
    # The observed price at each five-minute boundary is the VWAP of its
    # immediately preceding completed minute. Never use five-minute VWAP or
    # five-minute close as a silent substitute for the minute observation.
    select=time.minute%5==4;sample=frame.loc[select].copy();observed=time[select]
    vwap=sample.quote_volume/sample.volume.replace(0,np.nan)
    valid=vwap.notna()
    assert ((vwap[valid]>=sample.low[valid]-1e-8)&(vwap[valid]<=sample.high[valid]+1e-8)).all(),name
    result=pd.DataFrame({'date':observed-pd.Timedelta(minutes=4),'available_at':observed+pd.Timedelta(minutes=1),
                         'vwap':vwap.to_numpy(),'base_volume':sample.volume.to_numpy(),
                         'quote_volume':sample.quote_volume.to_numpy()})
    # Reconcile execution-price closes against independently stored native5m
    # observations, without replacing either data source when they disagree.
    original=ROOT/f'user_data/crypto_quant/tsmom_risk_native_20261114_v1/candles/futures/{sym}_USDT_USDT-5m-futures.feather'
    old=pd.read_feather(original).set_index('date').close.reindex(result.date).to_numpy()
    assert np.isfinite(old).all(),str(original)
    different=int(np.count_nonzero(old!=sample.close.to_numpy()))
    target=OUT/'monthly'/f'{sym}_{month}.parquet'
    if target.exists():pd.testing.assert_frame_equal(pd.read_parquet(target),result,check_exact=True)
    else:result.to_parquet(target,index=False)
    return {'symbol':sym,'month':month,'url':url,'archive':str(path),'archive_sha256':sha(path),
            'checksum':str(check),'checksum_sha256':sha(check),'minute_rows':len(frame),
            'sample_rows':len(result),'zero_volume_samples':int(result.vwap.isna().sum()),
            'native5m_close_differences':different,'parent_native5m':str(original),
            'parent_native5m_sha256':sha(original),'sample_path':str(target),'sample_sha256':sha(target)}

def main():
    assert not (HERE/'input_admission.json').exists()
    for d in ['archives','monthly','features']:(OUT/d).mkdir(parents=True,exist_ok=True)
    rows=[];failures=[]
    with ThreadPoolExecutor(max_workers=4)as pool:
        jobs={pool.submit(task,s,m):(s,m)for s in SYMBOLS for m in MONTHS}
        for future in as_completed(jobs):
            try:row=future.result();rows.append(row);print('ADMITTED',row['symbol'],row['month'],row['minute_rows'],'rows',row['zero_volume_samples'],'unknown',flush=True)
            except Exception as exc:failures.append({'task':jobs[future],'error':repr(exc)});print('FAILED',jobs[future],repr(exc),flush=True)
            save(HERE/'preparation_checkpoint.json',{'receipts':sorted(rows,key=lambda r:(r['symbol'],r['month'])),'failures':failures})
    if failures:raise RuntimeError('Preserve failures; no input admission')
    combined=[]
    for s in SYMBOLS:
        selected=sorted([r for r in rows if r['symbol']==s],key=lambda r:r['month'])
        frame=pd.concat([pd.read_parquet(r['sample_path'])for r in selected],ignore_index=True)
        expected=pd.date_range('2024-12-01','2026-08-31 23:55',freq='5min',tz='UTC')
        assert pd.DatetimeIndex(frame.date).equals(expected)
        path=OUT/'features'/f'{s}_minute_vwap_5m.parquet'
        if path.exists():pd.testing.assert_frame_equal(pd.read_parquet(path),frame,check_exact=True)
        else:frame.to_parquet(path,index=False)
        combined.append({'pair':s+'/USDT:USDT','path':str(path),'sha256':sha(path),'rows':len(frame),'unknown_samples':int(frame.vwap.isna().sum())})
    save(HERE/'input_admission.json',{'passed_archive_grid_contract':True,'source_universe':'local fixed8 native-available majors; not paper51spot currencies','symbols':SYMBOLS,'pairs':[s+'/USDT:USDT'for s in SYMBOLS],'native_datadir':str(ROOT/'user_data/crypto_quant/tsmom_risk_native_20261114_v1/candles'),'out':str(OUT),'minute_vwap_inputs':combined,'archive_receipts':sorted(rows,key=lambda r:(r['symbol'],r['month'])),'known_at':'one-minute VWAP at five-minute boundary after minute completion; daily feature usable only after completedUTCday','zero_volume_policy':'UNKNOWN, no carry or close substitute','funded_accounts':0})

if __name__=='__main__':main()
