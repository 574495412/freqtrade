"""Acquire checksum-verified public mark archives into a new data-only package."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
from pathlib import Path
import shutil
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PLAN = HERE / 'mark5m_plan.json'
MAX_ARCHIVE_BYTES = 10 * 1024**2
MAX_CSV_BYTES = 32 * 1024**2
MIN_FREE = 10 * 1024**3
IO_LOCK = threading.Lock()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_new(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')


def download(url, limit):
    if not url.startswith('https://data.binance.vision/data/futures/um/'):
        raise ValueError('Only the registered official public data host is permitted')
    attempts=[]
    for attempt in range(1,4):
        try:
            with urlopen(Request(url,headers={'User-Agent':'FreqtradeResearchDataAudit/1'}),timeout=30) as response:
                payload=response.read(limit+1)
                if len(payload)>limit:raise ValueError('Download size limit exceeded')
                return payload,{'url':url,'final_url':response.url,'http_status':response.status,
                    'observed_at_utc':datetime.now(timezone.utc).isoformat(),'bytes':len(payload),'sha256':sha(payload),
                    'attempts':attempts+[{'attempt':attempt,'status':response.status}]}
        except HTTPError as error:
            attempts.append({'attempt':attempt,'status':error.code})
            if error.code not in [429,500,502,503,504] or attempt==3:
                raise RuntimeError(json.dumps({'url':url,'attempts':attempts})) from error
        except (URLError,TimeoutError) as error:
            attempts.append({'attempt':attempt,'error_type':type(error).__name__})
            if attempt==3:raise RuntimeError(json.dumps({'url':url,'attempts':attempts})) from error
        time.sleep(5*attempt)
    raise AssertionError('Unreachable retry state')


def parse_archive(data, start, end):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if len(entries)!=1 or not entries[0].filename.endswith('.csv') or entries[0].file_size>MAX_CSV_BYTES:
            raise ValueError('Unexpected ZIP members or uncompressed size')
        with archive.open(entries[0]) as stream:
            frame=pd.read_csv(stream)
    required=['open_time','open','high','low','close','close_time']
    if not all(c in frame for c in required):raise ValueError('Missing mark archive columns')
    expected=pd.date_range(start,end,freq='5min',inclusive='left')
    dates=pd.DatetimeIndex(pd.to_datetime(frame.open_time,unit='ms',utc=True))
    if not dates.equals(expected):raise ValueError('Archive date grid differs; no gap filling permitted')
    if not np.array_equal(frame.close_time.to_numpy(dtype=np.int64),frame.open_time.to_numpy(dtype=np.int64)+299999):
        raise ValueError('Unexpected close time')
    values=frame[['open','high','low','close']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values<=0).any():raise ValueError('Invalid mark OHLC')
    if (frame.high<frame[['open','close']].max(axis=1)).any() or (frame.low>frame[['open','close']].min(axis=1)).any() or (frame.high<frame.low).any():
        raise ValueError('Mark OHLC bounds invalid')
    result=frame[['open','high','low','close']].copy()
    result.insert(0,'date',dates)
    return result


def acquire(job, output):
    directory=output/'archives'/job['symbol']/job['frequency']
    directory.mkdir(parents=True,exist_ok=True)
    archive_path=directory/job['filename']
    receipt_path=directory/(job['filename']+'.receipt.json')
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if receipt['state']!='completed':return receipt
        if sha(archive_path.read_bytes())!=receipt['archive']['sha256']:
            raise ValueError('Existing successful archive changed')
        return receipt
    if archive_path.exists():raise ValueError('Unreceipted archive requires explicit recovery review')
    if shutil.disk_usage(output).free<MIN_FREE:raise ValueError('Less than10GiB available disk')
    receipt={'job':job,'state':'starting','orders_enabled':False,'financial_runs':0}
    try:
        checksum, checksum_receipt=download(job['url']+'.CHECKSUM',4096)
        fields=checksum.decode('ascii').strip().split()
        if len(fields)!=2 or fields[1].lstrip('*')!=job['filename'] or len(fields[0])!=64:
            raise ValueError('Unexpected official checksum format')
        payload, payload_receipt=download(job['url'],MAX_ARCHIVE_BYTES)
        if sha(payload)!=fields[0]:raise ValueError('Official archive checksum mismatch')
        frame=parse_archive(payload,job['start'],job['end_exclusive'])
        with archive_path.open('xb') as stream:stream.write(payload)
        with archive_path.with_name(archive_path.name+'.CHECKSUM').open('xb') as stream:stream.write(checksum)
        receipt.update(state='completed',archive=payload_receipt,checksum=checksum_receipt,
                       official_checksum_verified=True,rows=len(frame),
                       first=frame.date.iloc[0].isoformat(),last=frame.date.iloc[-1].isoformat(),
                       grid_complete=True,OHLC_valid=True)
    except Exception as error:
        receipt.update(state='failed',error_type=type(error).__name__,error=str(error))
    save_new(receipt_path,receipt)
    with IO_LOCK:
        with (output/'acquisition.jsonl').open('a') as stream:stream.write(json.dumps(receipt)+'\n');stream.flush()
    return receipt


def assemble(plan, output, receipts):
    if any(r['state']!='completed' for r in receipts):return []
    destinations=[]
    for symbol in plan['symbols']:
        frames=[]
        for job in plan['archives']:
            if job['symbol']==symbol:
                data=(output/'archives'/symbol/job['frequency']/job['filename']).read_bytes()
                frames.append(parse_archive(data,job['start'],job['end_exclusive']))
        combined=pd.concat(frames,ignore_index=True).sort_values('date')
        if combined.date.duplicated().any():raise ValueError('Duplicate archive dates')
        for window in plan['windows']:
            selected=combined.loc[(combined.date>=pd.Timestamp(window['start']))&(combined.date<pd.Timestamp(window['end_exclusive']))].reset_index(drop=True)
            expected=pd.date_range(window['start'],window['end_exclusive'],freq='5min',inclusive='left')
            if not pd.DatetimeIndex(selected.date).equals(expected):raise ValueError('Incomplete assembled window')
            path=output/'candles'/window['id']/f'{symbol}-5m-mark.feather';path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if not pd.read_feather(path).equals(selected):raise ValueError('Existing derived mark data differs')
            else:selected.to_feather(path)
            destinations.append({'symbol':symbol,'window':window['id'],'path':str(path.relative_to(ROOT)),
                'sha256':sha(path.read_bytes()),'rows':len(selected),'start':window['start'],
                'end_exclusive':window['end_exclusive'],'historical_received_at_certified':False})
    return destinations


def main():
    plan=json.loads(PLAN.read_text())
    if sha(Path(__file__).read_bytes())!=plan['downloader_sha256']:
        raise ValueError('Registered downloader changed')
    if len(plan['archives'])!=1000 or len(plan['symbols'])!=40 or plan['financial_runs_allowed']!=0:
        raise ValueError('Data-only scope differs')
    output=(ROOT/plan['output_directory']).resolve()
    if not output.is_relative_to(ROOT/'user_data/crypto_quant'):raise ValueError('Invalid isolated output')
    output.mkdir(parents=True,exist_ok=True)
    with (output/'worker.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen=output/'registered_plan.json'
        if frozen.exists():
            if frozen.read_bytes()!=PLAN.read_bytes():raise ValueError('Acquisition plan mismatch')
        else:shutil.copyfile(PLAN,frozen)
        receipts=[]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(acquire,job,output):job for job in plan['archives']}
            for future in as_completed(futures):
                try:receipt=future.result()
                except Exception as error:
                    receipt={'job':futures[future],'state':'failed','error_type':type(error).__name__,
                             'error':str(error),'orders_enabled':False,'financial_runs':0}
                receipts.append(receipt)
                if len(receipts)%50==0 or receipt['state']!='completed':
                    print(json.dumps({'completed_jobs':len(receipts),'failed':sum(r['state']!='completed' for r in receipts),
                        'last':receipt['job']['filename']}),flush=True)
        assembled=assemble(plan,output,receipts)
        report={'state':'completed' if len(assembled)==80 else 'incomplete','archives_requested':len(receipts),
            'archives_passed':sum(r['state']=='completed' for r in receipts),
            'failures':[r for r in receipts if r['state']!='completed'],'derived_files':assembled,
            'raw_archives_preserved':True,'plan_sha256':sha(PLAN.read_bytes()),
            'historical_received_at_certified':False,'executable_bid_ask_certified':False,
            'financial_runs':0,'orders_enabled':False,'existing_P1_artifacts_changed':False,
            'finished_at_utc':datetime.now(timezone.utc).isoformat()}
        save_new(output/'acquisition_result.json',report)
        print(json.dumps({k:v for k,v in report.items() if k not in ['derived_files','failures']}),flush=True)


if __name__=='__main__':main()
