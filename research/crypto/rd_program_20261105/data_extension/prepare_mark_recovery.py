"""Preserve failed monthly archives and derive bounded official API gap requests."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
from pathlib import Path
import shutil
import zipfile

import numpy as np
import pandas as pd

import download_mark5m as source


def recover_archive(job, output):
    directory=output/'failed_monthly_raw'/job['symbol'];directory.mkdir(parents=True,exist_ok=True)
    payload,receipt=source.download(job['url'],source.MAX_ARCHIVE_BYTES)
    # Every returned payload is retained before checksum/schema/grid checks.
    path=directory/job['filename'];path.write_bytes(payload)
    checksum,check_receipt=source.download(job['url']+'.CHECKSUM',4096)
    (directory/(job['filename']+'.CHECKSUM')).write_bytes(checksum)
    if checksum.decode().split()[0]!=source.sha(payload):raise ValueError('Checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        entries=z.infolist()
        if len(entries)!=1 or entries[0].file_size>source.MAX_CSV_BYTES:raise ValueError('ZIP size/schema')
        with z.open(entries[0]) as stream:frame=pd.read_csv(stream)
    dates=pd.DatetimeIndex(pd.to_datetime(frame.open_time,unit='ms',utc=True))
    expected=pd.date_range(job['start'],job['end_exclusive'],freq='5min',inclusive='left')
    if dates.has_duplicates or not dates.is_monotonic_increasing or len(dates.difference(expected)):
        raise ValueError('Unexpected duplicate/unordered/extra timestamps')
    values=frame[['open','high','low','close']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values<=0).any():raise ValueError('Invalid mark price')
    if (frame.high<frame[['open','close']].max(axis=1)).any() or (frame.low>frame[['open','close']].min(axis=1)).any():
        raise ValueError('Invalid mark bounds')
    if not np.array_equal(frame.close_time.to_numpy(dtype=np.int64),frame.open_time.to_numpy(dtype=np.int64)+299999):
        raise ValueError('Close time mismatch')
    missing=expected.difference(dates)
    if len(missing)>576:raise ValueError('Gap exceeds preregistered bound for one archive')
    clusters=[]
    for moment in missing:
        if not clusters or moment-clusters[-1][-1]!=pd.Timedelta(minutes=5):clusters.append([])
        clusters[-1].append(moment)
    if len(clusters)>1:raise ValueError('More than one missing contiguous interval per failed month')
    requests=[]
    for group in clusters:
        key=job['symbol']+'_'+group[0].strftime('%Y%m%dT%H%M')
        requests.append({'id':key,'symbol':job['symbol'],'interval':'5m',
            'startTime':int(group[0].timestamp()*1000),
            'endTime':int((group[-1]+pd.Timedelta(minutes=5)).timestamp()*1000)-1,
            'limit':len(group),'expected_open_times_ms':[int(t.timestamp()*1000) for t in group],
            'parent_archive':str(path.relative_to(source.ROOT)),
            'parent_archive_sha256':source.sha(payload),
            'output_relative_path':f'api_raw/{key}.json'})
    result={'job':job,'raw_archive':str(path.relative_to(source.ROOT)),'download':receipt,
        'checksum':check_receipt,'actual_rows':len(frame),'expected_rows':len(expected),
        'missing_count':len(missing),'requests':requests,'original_archive_modified':False}
    source.save_new(directory/(job['filename']+'.diagnosis.json'),result)
    return result


def main():
    registration=source.HERE/'mark5m_recovery_registration.json';plan=json.loads(registration.read_text())
    if source.sha(Path(__file__).read_bytes())!=plan['preparer_sha256']:raise ValueError('Registered preparer changed')
    original=source.ROOT/plan['initial_acquisition_result']['path']
    if source.sha(original.read_bytes())!=plan['initial_acquisition_result']['sha256']:raise ValueError('Original result changed')
    failed=json.loads(original.read_text())['failures']
    if len(failed)!=80:raise ValueError('Failed archive count differs')
    output=source.ROOT/plan['output_directory'];output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(registration,output/'recovery_registration.json')
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(recover_archive,r['job'],output) for r in failed]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results)%20==0:print(json.dumps({'preserved_and_diagnosed':len(results)}),flush=True)
    results.sort(key=lambda r:(r['job']['symbol'],r['job']['filename']))
    requests=[request for result in results for request in result['requests']]
    if len(requests)>80 or sum(r['limit'] for r in requests)>34560:raise ValueError('API recovery budget exceeded')
    for symbol in plan['symbols']:
        selected=[r for r in requests if r['symbol']==symbol]
        if len(selected)>2 or sum(r['limit'] for r in selected)>864:raise ValueError('Symbol gap budget exceeded')
    source.save_new(output/'archive_diagnosis.json',{'archive_count':len(results),'results':results,
        'financial_runs':0,'raw_archives_preserved_before_validation':True,
        'correction_to_v001_summary':'raw_archives_preserved=true applied only to its 920 passed archives. Its 80 failed-grid payloads were not saved; this package now preserves separately re-fetched official bytes.'})
    source.save_new(output/'api_requests.json',{'registration_sha256':source.sha(registration.read_bytes()),
        'requests':requests,'max_concurrent_requests':2,'financial_runs':0,'orders_enabled':False,
        'expected_rows':sum(r['limit'] for r in requests)})
    print(json.dumps({'requests':len(requests),'expected_rows':sum(r['limit'] for r in requests),'financial_runs':0}),flush=True)


if __name__=='__main__':main()
