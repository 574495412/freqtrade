"""Add source-universe warmup via official archives; preserve all old inputs."""
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/residual_momentum_source_native_20261114_v1'
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
SYMBOLS=['ADA','BNB','BTC','DOGE','ETH','LINK','LTC','XRP']
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')

def main():
    assert not (HERE/'input_admission.json').exists()
    OUT.mkdir(exist_ok=True);(OUT/'candles/futures').mkdir(parents=True,exist_ok=True);(OUT/'archives').mkdir(exist_ok=True)
    native=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    assert native['commit']=='c051e9065c5b677f03817dd1f83cb980af1ed684'
    for p,h in native['files'].items():assert sha(ENGINE/p)==h,p
    sys.path.insert(0,str(ENGINE))
    from freqtrade.data.history.datahandlers import get_datahandler
    from freqtrade.enums import CandleType
    handler=get_datahandler(OUT/'candles','feather');receipts=[];sources=[]
    old=ROOT/'user_data/crypto_quant/tsmom_risk_native_20261114_v1/candles/futures'
    for sym in SYMBOLS:
        pair=sym+'/USDT:USDT';filename=sym+'USDT-1h-2024-11.zip'
        url=f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}USDT/1h/{filename}'
        archive=OUT/'archives'/filename;check_path=archive.with_suffix('.zip.CHECKSUM')
        for path,address in [(archive,url),(check_path,url+'.CHECKSUM')]:
            if not path.exists():
                response=requests.get(address,timeout=30);response.raise_for_status();path.write_bytes(response.content)
        assert check_path.read_text().split()[0]==sha(archive)
        frame=pd.read_csv(zipfile.ZipFile(archive).open(filename[:-4]+'.csv'))
        assert {'open_time','open','high','low','close','volume'}<=set(frame)
        frame['date']=pd.to_datetime(frame.open_time,unit='ms',utc=True)
        frame=frame[['date','open','high','low','close','volume']]
        parent=old/f'{sym}_USDT_USDT-1h-futures.feather';prior=pd.read_feather(parent)
        assert frame.date.iloc[0]==pd.Timestamp('2024-11-01',tz='UTC') and len(frame)==720
        assert frame.date.iloc[-1]+pd.Timedelta(hours=1)==prior.date.iloc[0]
        joined=pd.concat([frame,prior],ignore_index=True)
        assert pd.DatetimeIndex(joined.date).equals(pd.date_range('2024-11-01','2026-08-31 23:00',tz='UTC',freq='h'))
        joined=joined.astype({c:float for c in ['open','high','low','close','volume']})
        target=OUT/f'candles/futures/{sym}_USDT_USDT-1h-futures.feather'
        if not target.exists():handler.ohlcv_store(pair,'1h',joined,CandleType.FUTURES)
        loaded=handler.ohlcv_load(pair,'1h',candle_type=CandleType.FUTURES,fill_missing=False,drop_incomplete=False)
        pd.testing.assert_frame_equal(joined,loaded,check_exact=True)
        pd.testing.assert_frame_equal(loaded.iloc[720:].reset_index(drop=True),prior,check_exact=True,check_dtype=False)
        target=OUT/f'candles/futures/{sym}_USDT_USDT-1h-futures.feather'
        receipts.append({'symbol':sym,'url':url,'archive':str(archive),'archive_sha256':sha(archive),'checksum':str(check_path),'checksum_sha256':sha(check_path),'rows_added':720})
        sources.append({'pair':pair,'parent':str(parent),'parent_sha256':sha(parent),'path':str(target),'sha256':sha(target),'rows':len(joined),'old_rows_unchanged':True})
        save(HERE/'preparation_checkpoint.json',{'archive_receipts':receipts,'native_inputs':sources})
        print('ADMITTED',sym,len(joined),'hours;720 added;15336 old rows unchanged',flush=True)
    save(HERE/'input_admission.json',{'created_utc':datetime.now(UTC).isoformat(),'native_commit':native['commit'],'native_original_files_verified':len(native['files']),'datadir':str(OUT/'candles'),'source_universe_from_notebook_output':SYMBOLS,'pairs':[s+'/USDT:USDT'for s in SYMBOLS],'native_inputs':sources,'archive_receipts':receipts,'source_original_cleaned_dataset_available':False,'local_return_definition':'native perpetual simple close/previous-close minus1;source ret_1h cleaning provenance unavailable','missing_policy':'strictjoint observed hourly grid; no ffill or zero returns','stock_results_reused':False,'financial_accounts':0})

if __name__=='__main__':main()
