"""Admit the source's full active pool; preserve checksummed raw inputs/gaps.

No factor, portfolio, trade or return is calculated. Existing files are never
modified. Missing price histories are extended from official public archives.
"""
import hashlib
import io
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/funding_cross_section_native_20261114_v1'
SYMBOLS=['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX','LINK','TRX','DOT','LTC','BCH','NEAR','APT','ARB','OP','SUI','INJ','TIA','SEI','RUNE','FIL','ATOM','UNI','AAVE','ETC','XLM','ALGO']
EXTRA=['APT','ARB','OP','SUI','INJ','TIA','SEI','XLM']
OLD40=ROOT/'user_data/crypto_quant/nfi40_data_v002/candles/futures'
OLD80=ROOT/'user_data/crypto_quant/nfi80_historical_universe_20261109_v001/candles/futures'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def capture(task):
    symbol,kind,month=task
    interval=kind.split(':')[-1]
    name=f'{symbol}USDT-fundingRate-{month}.zip' if kind=='fundingRate' else f'{symbol}USDT-{interval}-{month}.zip'
    if kind=='fundingRate':folder=f'fundingRate/{symbol}USDT'
    elif kind.startswith('mark:'):folder=f'markPriceKlines/{symbol}USDT/{interval}'
    else:folder=f'klines/{symbol}USDT/{interval}'
    url=f'https://data.binance.vision/data/futures/um/monthly/{folder}/{name}'
    path=OUT/'raw'/kind.replace(':','_')/name
    path.parent.mkdir(parents=True,exist_ok=True)
    checksum=path.with_suffix('.CHECKSUM')
    if not checksum.exists():
        response=requests.get(url+'.CHECKSUM',timeout=30);response.raise_for_status();checksum.write_bytes(response.content)
    expected=checksum.read_text().split()[0]
    reused=None
    if not path.exists():
        for source in REUSE.get(name,[]):
            if sha(source)==expected:
                os.link(source,path);reused=str(source);break
        if not path.exists():
            response=requests.get(url,timeout=30);response.raise_for_status();path.write_bytes(response.content)
    if sha(path)!=expected:raise ValueError('Official archive checksum conflict: '+name)
    with ZipFile(path) as archive:
        frame=pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
    return task,frame,dict(url=url,path=str(path),sha256=expected,checksum_sha256=sha(checksum),reused_identical_source=reused)


def merge_prices(symbol,kind,frames):
    suffix={'price:1h':'1h-futures','price:5m':'5m-futures','mark:1h':'1h-mark'}[kind]
    source=OLD80/f'{symbol}_USDT_USDT-{suffix}.feather'
    old=pd.read_feather(source)[['date','open','high','low','close','volume']]
    prepared=[]
    for frame in frames:
        frame=frame.copy();frame['date']=pd.to_datetime(frame['open_time'],unit='ms',utc=True)
        if 'volume' not in frame:frame['volume']=0.0
        prepared.append(frame[['date','open','high','low','close','volume']].astype({c:'float64' for c in ['open','high','low','close','volume']}))
    new=pd.concat(prepared,ignore_index=True).sort_values('date')
    if new.date.duplicated().any():raise ValueError('Duplicate official price clock')
    shared=old.set_index('date').index.intersection(new.set_index('date').index)
    if len(shared):
        a=old.set_index('date').loc[shared];b=new.set_index('date').loc[shared]
        if not np.allclose(a.to_numpy(),b.to_numpy(),rtol=0,atol=1e-7):raise ValueError('Raw/old price overlap conflict')
    merged=pd.concat([new,old.loc[~old.date.isin(new.date)]],ignore_index=True).sort_values('date')
    start='2025-01-01T00:00Z' if kind=='price:5m' else '2024-12-01T00:00Z'
    merged=merged.loc[merged.date.ge(pd.Timestamp(start)) & merged.date.lt(pd.Timestamp('2026-09-01T00:00Z'))].reset_index(drop=True)
    freq='5min' if kind=='price:5m' else 'h'
    expected=pd.date_range(start,'2026-09-01T00:00Z',inclusive='left',freq=freq)
    if not pd.DatetimeIndex(merged.date).as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Complete price grid missing: '+symbol+' '+kind)
    if not np.isfinite(merged[['open','high','low','close','volume']]).all().all() or merged[['open','high','low','close']].le(0).any().any():raise ValueError('Invalid price input')
    target=OUT/'candles/futures'/source.name
    merged.to_feather(target)
    return dict(symbol=symbol,kind=kind,rows=len(merged),first=str(merged.date.iloc[0]),last=str(merged.date.iloc[-1]),old_source=str(source),old_source_sha256=sha(source),target_sha256=sha(target))


def main():
    if (HERE/'input_admission.json').exists():raise FileExistsError('Frozen admission already exists')
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'candles/futures').mkdir(parents=True,exist_ok=True)
    global REUSE
    REUSE={}
    for root in [ROOT/'user_data/crypto_quant/spread_liquidity_native_20261114_v1/raw',ROOT/'user_data/crypto_quant/rd_program_20261105_v1']:
        for p in root.rglob('*fundingRate*.zip'):REUSE.setdefault(p.name,[]).append(p)
    tasks=[(s,'fundingRate',d.strftime('%Y-%m')) for s in SYMBOLS for d in pd.date_range('2024-12-01','2026-08-01',freq='MS')]
    for s in EXTRA:
        for kind,end in [('price:1h','2025-08-01'),('price:5m','2025-11-01'),('mark:1h','2025-12-01')]:
            start='2025-01-01' if kind=='price:5m' else '2024-12-01'
            tasks.extend((s,kind,d.strftime('%Y-%m')) for d in pd.date_range(start,end,freq='MS'))
    save(HERE/'input_request_plan.json',dict(before_factor_or_account_outputs=True,source_pool_count=30,active_pool_count=29,
        source_explicit_matic_mask='MATIC invalid after2024-09-10; no POL continuation substituted',symbols=SYMBOLS,
        tasks=tasks,source_manifest_sha256=sha(HERE/'source_manifest.json'),
        start='2025-01-01T00:00Z',end='2026-09-01T00:00Z',raw_funding_start='2024-12-01',
        network_public_only=True,source_returns_adopted=False,minimum_new_native_worker_free_bytes=2*1024**3))
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched=[]
        for result in pool.map(capture,tasks):
            fetched.append(result)
            if len(fetched)%50==0:
                save(OUT/'capture_progress.json',dict(completed=len(fetched),tasks=len(tasks),free_bytes=shutil.disk_usage(ROOT).free,at_utc=datetime.now(UTC).isoformat()))
                print('captured',len(fetched),'/',len(tasks),flush=True)
    save(OUT/'public_receipts.json',[v[2] for v in fetched])
    quotes=[];funding=[];issues=[];merged=[];links=[]
    for symbol in SYMBOLS:
        pieces=[v[1] for v in fetched if v[0][0]==symbol and v[0][1]=='fundingRate']
        raw=pd.concat(pieces,ignore_index=True).sort_values('calc_time')
        raw['event_time']=pd.to_datetime(raw.calc_time,unit='ms',utc=True)
        raw['date']=raw.event_time.dt.floor('h');raw['pair']=symbol+'/USDT:USDT'
        if raw.date.duplicated().any() or not np.isfinite(raw.last_funding_rate).all():raise ValueError('Duplicate/nonfinite funding')
        if not raw.funding_interval_hours.isin([1,2,4,8]).all():raise ValueError('Unsupported source funding cadence')
        deltas=raw.date.diff().dt.total_seconds().div(3600)
        plausible=deltas.eq(raw.funding_interval_hours) | deltas.eq(raw.funding_interval_hours.shift(1)) | deltas.isna()
        for row in raw.loc[~plausible].itertuples():issues.append(dict(symbol=symbol,event_time=str(row.event_time),kind='funding_calendar_gap',row_index=int(row.Index)))
        # All known days must include an actual event at 00; no zero-fill for missing rows.
        expected_days=pd.date_range('2024-12-01','2026-08-31',tz='UTC',freq='D')
        midnight=pd.DatetimeIndex(raw.loc[raw.date.dt.hour.eq(0),'date'])
        for day in expected_days.difference(midnight):issues.append(dict(symbol=symbol,date=str(day),kind='missing_midnight_event'))
        funding.append(raw[['pair','event_time','date','funding_interval_hours','last_funding_rate']])
        folder=OUT/'candles/futures'
        for suffix in ['1d-futures','1h-futures','5m-futures','1h-mark']:
            source=(OLD80 if symbol in EXTRA else OLD40)/f'{symbol}_USDT_USDT-{suffix}.feather';target=folder/source.name
            if symbol in EXTRA and suffix!='1d-futures':continue
            if target.exists():
                if sha(target)!=sha(source):raise ValueError('Existing frozen source conflict')
            else:os.link(source,target)
            links.append(dict(source=str(source),target=str(target),sha256=sha(source)))
        # Funding records use exact public rates and nominal hour keys, matching native exchange semantics.
        native=raw[['date','last_funding_rate']].rename(columns={'last_funding_rate':'funding_rate'})
        prior=(OLD80 if symbol in EXTRA else OLD40)/f'{symbol}_USDT_USDT-1h-funding_rate.feather'
        existing=pd.read_feather(prior).set_index('date').funding_rate
        common=existing.index.intersection(pd.DatetimeIndex(native.date))
        a=existing.reindex(common).to_numpy();b=native.set_index('date').funding_rate.reindex(common).to_numpy()
        if not np.allclose(a,b,rtol=0,atol=1e-12):raise ValueError('Native/raw funding rate conflict: '+symbol)
        native.to_feather(folder/prior.name)
    for symbol in EXTRA:
        for kind in ['price:1h','price:5m','mark:1h']:
            merged.append(merge_prices(symbol,kind,[v[1] for v in fetched if v[0][0]==symbol and v[0][1]==kind]))
    pd.concat(funding,ignore_index=True).to_parquet(OUT/'funding_events.parquet',index=False)
    data={str(p.relative_to(OUT/'candles')):sha(p) for p in sorted((OUT/'candles/futures').glob('*.feather'))}
    save(OUT/'data_receipts.json',data)
    save(HERE/'input_admission.json',dict(passed=not issues,scope='source30 with source-explicit MATIC mask;29active,not point-in-time universe',
        active_pairs=[s+'/USDT:USDT' for s in SYMBOLS],raw_archives=len(fetched),funding_rows=sum(len(f) for f in funding),
        issues=issues,merged_prices=merged,hardlinked_frozen_sources=links,
        data_receipts=dict(path=str(OUT/'data_receipts.json'),sha256=sha(OUT/'data_receipts.json')),
        funding_events=dict(path=str(OUT/'funding_events.parquet'),sha256=sha(OUT/'funding_events.parquet')),
        public_receipts=dict(path=str(OUT/'public_receipts.json'),sha256=sha(OUT/'public_receipts.json')),
        private_settlement_certified=False))
    print('INPUT FINISHED; admitted=',not issues,'issues=',len(issues),'archives=',len(fetched),flush=True)


if __name__=='__main__':main()
