"""Checksum official spot archives, strict native storage, immutable futures links."""
import concurrent.futures
import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/pairs_cointegration_native_20261114_v1'
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,default=str)+'\n')
def ref(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))


def fetch(symbol,month):
    archive=OUT/'spot_archives'/f'{symbol}USDT-1d-{month}.zip'
    checksum=archive.with_suffix('.zip.CHECKSUM')
    url=f'https://data.binance.vision/data/spot/monthly/klines/{symbol}USDT/1d/{archive.name}'
    for path,address in [(archive,url),(checksum,url+'.CHECKSUM')]:
        if not path.exists():
            data=urllib.request.urlopen(urllib.request.Request(address,headers={'User-Agent':'quant-research'}),timeout=45).read()
            path.write_bytes(data)
    expected=checksum.read_text().split()[0]
    if sha(archive)!=expected:raise ValueError('Official checksum mismatch '+archive.name)
    with zipfile.ZipFile(archive) as z:
        if z.namelist()!=[archive.name.replace('.zip','.csv')]:raise ValueError('Unexpected archive members')
        frame=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])),header=None)
    if frame.shape[1]!=12:raise ValueError('Unexpected spot schema')
    stamp=frame[0].to_numpy(dtype=np.int64)
    unit='us' if stamp[0]>10**14 else 'ms'
    dates=pd.to_datetime(stamp,unit=unit,utc=True)
    close_time=pd.to_datetime(frame[6].to_numpy(dtype=np.int64),unit=unit,utc=True)
    tick=pd.Timedelta(microseconds=1) if unit=='us' else pd.Timedelta(milliseconds=1)
    if not (close_time==dates+pd.Timedelta(days=1)-tick).all():raise ValueError('Incomplete daily clock')
    values=frame.iloc[:,1:6].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values<=0).any():raise ValueError('Unknown/nonpositive daily OHLCV')
    result=pd.DataFrame(values,columns=['open','high','low','close','volume'])
    result.insert(0,'date',dates)
    return symbol,result,dict(symbol=symbol,month=month,url=url,archive=ref(archive),checksum=ref(checksum),unit=unit,rows=len(result))


def main():
    protocol=json.loads((HERE/'protocol.json').read_text())
    if not (HERE/'protocol_freeze.json').exists():
        save(HERE/'protocol_freeze.json',dict(utc=datetime.now(UTC).isoformat(),protocol=ref(HERE/'protocol.json'),before_local_selection=True,before_any_account=True))
    else:
        assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['protocol']['sha256']
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'spot_archives').mkdir(exist_ok=True)
    directory=OUT/'candles';(directory/'futures').mkdir(parents=True,exist_ok=True)
    parent=ROOT/'research/crypto/tsmom_risk_native_20261114_v1'
    base=ROOT/'user_data/crypto_quant/tsmom_risk_native_20261114_v1/candles'
    expected=json.loads((base.parent/'data_receipts.json').read_text())
    receipts={};links=[]
    for symbol in protocol['symbols']:
        for timeframe,kind in [('1h','futures'),('5m','futures'),('1d','futures'),('1h','mark'),('1h','funding_rate')]:
            relative=f'futures/{symbol}_USDT_USDT-{timeframe}-{kind}.feather'
            src=base/relative;dst=directory/relative
            assert sha(src)==expected[relative]
            if not dst.exists():os.link(src,dst)
            assert sha(dst)==expected[relative]
            receipts[relative]=sha(dst);links.append(dict(source=str(src),target=str(dst),sha256=sha(dst)))
    months=pd.period_range('2020-08','2026-08',freq='M').astype(str)
    frames={s:[] for s in protocol['symbols']};sources=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures=[executor.submit(fetch,s,m) for s in protocol['symbols'] for m in months]
        for i,future in enumerate(concurrent.futures.as_completed(futures),1):
            symbol,frame,record=future.result();frames[symbol].append(frame);sources.append(record)
            if i%50==0:print(f'Official spot archives verified {i}/{len(futures)}',flush=True)
    sys.path.insert(0,str(ENGINE))
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType
    handler=FeatherDataHandler(directory);grid=pd.date_range('2020-08-18','2026-08-31',freq='D',tz='UTC');panel={}
    for symbol in protocol['symbols']:
        frame=pd.concat(frames[symbol],ignore_index=True).sort_values('date')
        frame=frame[frame.date.between(grid[0],grid[-1])].reset_index(drop=True)
        if not pd.DatetimeIndex(frame.date).equals(grid):raise ValueError('Missing/duplicate original spot grid '+symbol)
        if (frame.low>frame[['open','close']].min(axis=1)).any() or (frame.high<frame[['open','close']].max(axis=1)).any():raise ValueError('OHLC range conflict')
        pair=symbol+'/USDT';handler.ohlcv_store(pair,'1d',frame,CandleType.SPOT)
        loaded=load_pair_history(pair,'1d',directory,fill_up_missing=False,drop_incomplete=False,candle_type=CandleType.SPOT)
        pd.testing.assert_frame_equal(frame,loaded,check_exact=True,check_dtype=False)
        file=directory/f'{symbol}_USDT-1d.feather';receipts[file.name]=sha(file);panel[symbol]=loaded.set_index('date').close
    pd.DataFrame(panel).to_parquet(OUT/'spot_daily.parquet')
    save(OUT/'data_receipts.json',receipts)
    save(HERE/'input_admission.json',dict(passed=True,protocol=ref(HERE/'protocol.json'),source_manifest=ref(HERE/'source_manifest.json'),
         spot_grid_days=len(grid),spot_rows=len(grid)*10,archives=sorted(sources,key=lambda r:(r['symbol'],r['month'])),futures_links=links,
         native_data_receipts=ref(OUT/'data_receipts.json'),price_panel=ref(OUT/'spot_daily.parquet'),parent_admission=ref(parent/'input_admission.json'),
         observed_tia_gap_exposure='Source10coin universe excludes TIA independently of local returns; previous TIA failure retained',
         actual_exchange_settlement_certified=False,historical_rules_certified=False,point_in_time_universe=False))
    print('INPUT ADMITTED:10 source symbols,730 officialspot ZIPs,50 byte-exact futures links',flush=True)


if __name__=='__main__':main()
