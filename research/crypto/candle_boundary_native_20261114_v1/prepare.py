"""Verify/reuse public archives; stage native OHLCV, never simulate accounts."""
import hashlib,json,os,zipfile
from datetime import UTC,datetime
from pathlib import Path
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/candle_boundary_native_20261114_v1'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,default=str,ensure_ascii=False)+'\n')
def ref(path):return {'path':str(path),'sha256':sha(path)}
def main():
    assert not (HERE/'input_admission.json').exists()
    folder=OUT/'candles/futures';folder.mkdir(parents=True,exist_ok=True)
    old=ROOT/'user_data/crypto_quant/signed_jump_native_20261114_v1/archives'
    frames=[];receipts=[]
    for month in pd.date_range('2024-12-01','2026-08-01',freq='MS').strftime('%Y-%m'):
        p=old/f'BTCUSDT-1m-{month}.zip';check=p.with_suffix('.zip.CHECKSUM')
        assert check.read_text().split()[0]==sha(p),p
        with zipfile.ZipFile(p)as archive:frame=pd.read_csv(archive.open(p.stem+'.csv'))
        time=pd.to_datetime(frame.open_time,unit='ms',utc=True)
        start=pd.Timestamp(month+'-01',tz='UTC');end=start+pd.offsets.MonthBegin(1)
        assert pd.DatetimeIndex(time).as_unit('ns').equals(pd.date_range(start,end,freq='min',inclusive='left').as_unit('ns'))
        frame=frame[['open','high','low','close','volume']].astype(float);frame.insert(0,'date',time)
        assert np.isfinite(frame.iloc[:,1:]).all().all() and frame.iloc[:,1:5].gt(0).all().all() and frame.volume.ge(0).all()
        assert frame.high.ge(frame[['open','close','low']].max(axis=1)).all() and frame.low.le(frame[['open','close','high']].min(axis=1)).all()
        receipts.append({'month':month,'archive':ref(p),'checksum':ref(check),'minute_rows':len(frame),'zero_volume_rows':int(frame.volume.eq(0).sum())})
        frames.append(frame)
    prices=pd.concat(frames,ignore_index=True)
    assert not prices.date.duplicated().any()
    path=folder/'BTC_USDT_USDT-1m-futures.feather';prices.to_feather(path)
    origin=ROOT/'user_data/crypto_quant/nfi40_data_v002/candles/futures'
    for suffix in ['5m-futures','1h-mark','1h-funding_rate']:
        p=origin/f'BTC_USDT_USDT-{suffix}.feather';target=folder/p.name
        os.link(p,target)
    # Reconcile all minute closes against independently staged5m execution bars.
    minute=prices.set_index('date').close
    fives=pd.read_feather(folder/'BTC_USDT_USDT-5m-futures.feather')
    selected=fives.loc[fives.date.ge(prices.date.iloc[0])&fives.date.le(prices.date.iloc[-1]-pd.Timedelta(minutes=4))]
    assert np.array_equal(minute.reindex(selected.date+pd.Timedelta(minutes=4)).to_numpy(),selected.close.to_numpy())
    save(OUT/'data_receipts.json',{str(p.relative_to(OUT/'candles')):sha(p)for p in sorted(folder.glob('*.feather'))})
    save(HERE/'input_admission.json',{'passed':True,'archives':receipts,'complete_1m_rows':len(prices),'native_5m_close_reconciliations':len(selected),'zero_volume_policy':'Retain actual zero-volume candles. Native execution rejection is audited, never impute volume.','prepared_at':datetime.now(UTC).isoformat(),'native_1m':ref(path),'account_results_computed':False})
    print('Verified21official archives; staged',len(prices),'1m observations;',len(selected),'5m close matches')
if __name__=='__main__':main()
