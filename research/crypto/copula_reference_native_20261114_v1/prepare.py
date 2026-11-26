"""Admit the full paper universe's hourly quotes, without accounts or imputation."""
import hashlib,json,os,shutil,zipfile
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import UTC,datetime
from pathlib import Path
import numpy as np
import pandas as pd
import requests
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/copula_reference_native_20261114_v1'
SYMBOLS=['BTC','ETH','BCH','XRP','EOS','LTC','TRX','ETC','LINK','XLM','ADA','XMR','DASH','ZEC','XTZ','ATOM','BNB','ONT','IOTA','BAT']
START=pd.Timestamp('2024-12-01',tz='UTC');END=pd.Timestamp('2026-09-01',tz='UTC');EOS_END=pd.Timestamp('2025-05-21 09:00',tz='UTC')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.writing');tmp.write_text(json.dumps(v,indent=2,default=str,ensure_ascii=False)+'\n');tmp.replace(p)
def ref(p):return {'path':str(p),'sha256':sha(p)}
def capture(task):
    symbol,month=task
    try:
        assert shutil.disk_usage(ROOT).free>=2*1024**3,'Need2GiBfree'
        name=f'{symbol}USDT-1h-{month}.zip';path=OUT/'archives'/name;check=path.with_suffix('.zip.CHECKSUM')
        url=f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}USDT/1h/{name}'
        for target,u in [(path,url),(check,url+'.CHECKSUM')]:
            if target.exists():continue
            for attempt in range(3):
                r=requests.get(u,timeout=30)
                if r.status_code==200:
                    temporary=target.with_suffix(target.suffix+'.download');temporary.write_bytes(r.content);temporary.replace(target);break
                (OUT/'archives'/(target.name+f'.HTTP{r.status_code}.attempt{attempt}')).write_bytes(r.content)
            else:raise RuntimeError(f'HTTP{r.status_code}:{u}')
        assert check.read_text().split()[0]==sha(path),'Official checksum mismatch'
        with zipfile.ZipFile(path)as archive:f=pd.read_csv(archive.open(path.stem+'.csv'))
        date=pd.to_datetime(f.open_time,unit='ms',utc=True);begin=pd.Timestamp(month+'-01',tz='UTC');finish=min(begin+pd.offsets.MonthBegin(1),EOS_END)if symbol=='EOS'else begin+pd.offsets.MonthBegin(1)
        terminal_rows=0
        if symbol=='EOS'and month=='2025-05':
            # The official archive also publishes a bar OPEN at the announced
            # terminal instant. Retain raw bytes; quarantine it from ordinary
            # tradable OHLCV because its interval begins after termination.
            tail=date.ge(EOS_END)
            assert int(tail.sum())==1 and date.loc[tail].iloc[0]==EOS_END
            terminal_rows=int(tail.sum());f=f.loc[~tail].reset_index(drop=True);date=date.loc[~tail].reset_index(drop=True)
        expected=pd.date_range(begin,finish,freq='h',inclusive='left')
        assert pd.DatetimeIndex(date).as_unit('ns').equals(expected.as_unit('ns')),f'Incomplete/extra hourly grid:{symbol}:{month}:{len(f)}:{date.iloc[-1]}'
        f=f[['open','high','low','close','volume']].astype(float);f.insert(0,'date',date)
        valid(f)
        dest=OUT/'monthly'/f'{symbol}_{month}.parquet';f.to_parquet(dest,index=False)
        return {'passed':True,'symbol':symbol,'month':month,'rows':len(f),'url':url,'archive':ref(path),'checksum':ref(check),'prepared':ref(dest),'terminal_observations_quarantined':terminal_rows}
    except Exception as exc:return {'passed':False,'symbol':symbol,'month':month,'error':repr(exc)}
def valid(f):
    assert np.isfinite(f.iloc[:,1:]).all().all()and f[['open','high','low','close']].gt(0).all().all()and f.volume.ge(0).all()
    assert f.high.ge(f[['open','low','close']].max(axis=1)).all()and f.low.le(f[['open','high','close']].min(axis=1)).all()
def main():
    assert not(HERE/'input_admission.json').exists()
    for d in ['archives','monthly','candles/futures']:(OUT/d).mkdir(parents=True,exist_ok=True)
    nfi=json.loads((ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/plan.json').read_text())
    wide=json.loads(Path(nfi['data_receipts']['path']).read_text())
    fund=json.loads((ROOT/'research/crypto/funding_cross_section_native_20261114_v1/input_admission.json').read_text());fd=Path(fund['data_receipts']['path']);fund_receipts=json.loads(fd.read_text())
    source_roots=[ROOT/'user_data/crypto_quant/nfi40_data_v002/candles',fd.parent/'candles']
    # First source is verified against its owning full dataset receipt, not the
    # ten-symbol child study's reduced file list.
    oldplan=json.loads((ROOT/'research/crypto/strategy_comparison_wide_20261105/plan.json').read_text())
    old_receipts=json.loads(Path(oldplan['data_receipts']['path']).read_text())
    reuse=[];todo=[]
    for symbol in SYMBOLS:
        relative=f'futures/{symbol}_USDT_USDT-1h-futures.feather';found=None
        for base,receipts in [(source_roots[0],old_receipts),(source_roots[1],fund_receipts)]:
            p=base/relative
            if not p.exists()or relative not in receipts:continue
            assert sha(p)==receipts[relative],p
            f=pd.read_feather(p);f=f.loc[f.date.ge(START)&f.date.lt(END)].reset_index(drop=True)
            if not pd.DatetimeIndex(f.date).as_unit('ns').equals(pd.date_range(START,END,freq='h',inclusive='left').as_unit('ns')):continue
            valid(f);found=(p,f);break
        if found:
            p,f=found;target=OUT/'candles'/relative;f.to_feather(target);reuse.append({'symbol':symbol,'source':ref(p),'target':ref(target),'rows':len(f)})
        else:
            last='2025-05-01'if symbol=='EOS'else'2026-08-01'
            todo.extend((symbol,d.strftime('%Y-%m'))for d in pd.date_range(START,pd.Timestamp(last,tz='UTC'),freq='MS'))
    save(HERE/'input_request_plan.json',{'source_universe':SYMBOLS,'window':[str(START),str(END)],'terminal_EOS':str(EOS_END),'EOS_known_source':ref(HERE/'sources/eos_notice_web_capture.txt'),'reused':reuse,'new_archive_tasks':todo,'financial_accounts':0,'no_universe_subset':True})
    rows=[]
    with ThreadPoolExecutor(max_workers=4)as pool:
        jobs={pool.submit(capture,t):t for t in todo}
        for future in as_completed(jobs):
            row=future.result();rows.append(row);save(HERE/'download_checkpoint.json',{'tasks':rows,'expected':len(todo)})
            print('ADMIT'if row['passed']else'FAIL',row['symbol'],row['month'],row.get('rows',row.get('error')),flush=True)
    if any(not r['passed']for r in rows):raise RuntimeError('Preserve input failure; no native financial admission')
    for symbol in sorted({s for s,m in todo}):
        records=sorted([r for r in rows if r['symbol']==symbol],key=lambda r:r['month']);f=pd.concat([pd.read_parquet(r['prepared']['path'])for r in records],ignore_index=True)
        expected=pd.date_range(START,EOS_END if symbol=='EOS'else END,freq='h',inclusive='left');assert pd.DatetimeIndex(f.date).as_unit('ns').equals(expected.as_unit('ns'))
        valid(f);f.to_feather(OUT/f'candles/futures/{symbol}_USDT_USDT-1h-futures.feather')
    receipts={str(p.relative_to(OUT/'candles')):sha(p)for p in sorted((OUT/'candles/futures').glob('*.feather'))};assert len(receipts)==20
    save(OUT/'data_receipts.json',receipts)
    save(HERE/'input_admission.json',{'passed_for_hourly_prices':True,'source_universe':SYMBOLS,'complete_source_members':20,'reused':reuse,'archives':rows,'data_receipts':ref(OUT/'data_receipts.json'),'terminal_EOS':str(EOS_END),'missing_postterminal_prices_imputed':False,'substituted_A_for_EOS':False,'native_funded_accounts':0,'financial_admission':False,'pending':['EOS retired market precision/lot/leverage metadata','Full native paired execution and lifecycle integration','Source-complete copula family and constrained Tawn estimation','Funding inputs and ownership verification','Frozen account matrix before any returns']})
    print('Complete20-member source hourly inputs;0financial accounts')
if __name__=='__main__':main()
