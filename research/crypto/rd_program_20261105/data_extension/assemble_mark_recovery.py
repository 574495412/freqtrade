"""Verify official archives/API gaps and write separate complete mark datasets."""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

import download_mark5m as source

INPUTS = {}


def read_json(path):
    payload=path.read_bytes();INPUTS[str(path.relative_to(source.ROOT))]=source.sha(payload)
    return json.loads(payload)


def archive_frame(path, digest, start, end, allow_gaps):
    payload=path.read_bytes()
    if source.sha(payload)!=digest:raise ValueError('Archive identity drift')
    INPUTS[str(path.relative_to(source.ROOT))]=digest
    checksum=path.with_name(path.name+'.CHECKSUM');body=checksum.read_bytes()
    INPUTS[str(checksum.relative_to(source.ROOT))]=source.sha(body)
    if body.decode().split()[0]!=digest:raise ValueError('Official checksum no longer matches')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info=archive.infolist()
        if len(info)!=1 or info[0].file_size>source.MAX_CSV_BYTES:raise ValueError('ZIP bounds')
        with archive.open(info[0]) as stream:raw=pd.read_csv(stream)
    return canonical_frame(raw,start,end,allow_gaps)


def canonical_frame(raw,start,end,allow_gaps=False):
    dates=pd.DatetimeIndex(pd.to_datetime(raw.open_time,unit='ms',utc=True)).as_unit('ns')
    expected=pd.date_range(start,end,freq='5min',inclusive='left').as_unit('ns')
    if dates.has_duplicates or not dates.is_monotonic_increasing or len(dates.difference(expected)):
        raise ValueError('Duplicate, unordered or extra data')
    if not allow_gaps and not dates.equals(expected):raise ValueError('Incomplete exact grid')
    values=raw[['open','high','low','close']].astype(float)
    if not np.isfinite(values.to_numpy()).all() or (values.to_numpy()<=0).any():raise ValueError('Invalid OHLC')
    if (values.high<values[['open','close']].max(axis=1)).any() or (values.low>values[['open','close']].min(axis=1)).any():
        raise ValueError('OHLC bounds')
    if not np.array_equal(raw.close_time.to_numpy(dtype=np.int64),raw.open_time.to_numpy(dtype=np.int64)+299999):
        raise ValueError('Close time differs')
    values.insert(0,'date',dates)
    return values


def api_frame(path,request):
    record=read_json(path)
    if record['state']!='completed' or record['request']!={k:request[k] for k in ['symbol','interval','startTime','endTime','limit']}:
        raise ValueError('API response/request identity differs')
    rows=record['response']['result']
    if len(rows)!=request['limit'] or [r[0] for r in rows]!=request['expected_open_times_ms']:
        raise ValueError('API supplied unexpected timestamps')
    raw=pd.DataFrame(rows,columns=['open_time','open','high','low','close','ignore5','close_time','ignore7','ignore8','ignore9','ignore10','ignore11'])
    start=pd.Timestamp(request['startTime'],unit='ms',tz='UTC');end=pd.Timestamp(request['endTime']+1,unit='ms',tz='UTC')
    return canonical_frame(raw,start,end)


def main():
    registration_path=source.HERE/'mark5m_assembly_registration.json';registration=read_json(registration_path)
    if source.sha(Path(__file__).read_bytes())!=registration['assembler_sha256']:raise ValueError('Registered assembler changed')
    initial=source.ROOT/registration['initial_package'];recovery=source.ROOT/registration['recovery_package']
    target=recovery/'assembled';target.mkdir(exist_ok=False)
    plan=read_json(source.HERE/'mark5m_plan.json')
    diagnosis=read_json(recovery/'archive_diagnosis.json')
    gaps=read_json(recovery/'api_requests.json')
    api_summary=read_json(recovery/'api_acquisition_summary.json')
    if api_summary['requests']!=80 or len(api_summary['results'])!=80 or any(r['state']!='completed' for r in api_summary['results']):
        raise ValueError('Incomplete API acquisition')
    failed={(r['job']['symbol'],r['job']['filename']):r for r in diagnosis['results']}
    old_plans=read_json(source.HERE.parent/'p0/environment/prepared_plans.json')
    records=[];origins=[];source_ids={}
    for job in plan['archives']:
        key=job['url'];source_ids[key]=len(origins)+1
        origins.append({'source_id':source_ids[key],'kind':'official_archive','url':key})
    for request in gaps['requests']:
        key=request['id'];source_ids[key]=len(origins)+1
        origins.append({'source_id':source_ids[key],'kind':'official_mark_API_gap','response_path':str((recovery/request['output_relative_path']).relative_to(source.ROOT))})
    for symbol in plan['symbols']:
        fragments=[]
        for job in [j for j in plan['archives'] if j['symbol']==symbol]:
            key=(symbol,job['filename'])
            if key in failed:
                old=failed[key];path=source.ROOT/old['raw_archive'];digest=old['download']['sha256']
                part=archive_frame(path,digest,job['start'],job['end_exclusive'],allow_gaps=True)
            else:
                path=initial/'archives'/symbol/job['frequency']/job['filename']
                receipt=read_json(path.with_name(path.name+'.receipt.json'))
                if receipt['state']!='completed':raise ValueError('Unexpected initial archive failure')
                part=archive_frame(path,receipt['archive']['sha256'],job['start'],job['end_exclusive'],allow_gaps=False)
            part['source_id']=np.int32(source_ids[job['url']]);fragments.append(part)
        for request in [r for r in gaps['requests'] if r['symbol']==symbol]:
            part=api_frame(recovery/request['output_relative_path'],request)
            part['source_id']=np.int32(source_ids[request['id']]);fragments.append(part)
        combined=pd.concat(fragments,ignore_index=True).sort_values('date')
        if combined.date.duplicated().any():raise ValueError('API overlaps existing archive observations')
        for window in plan['windows']:
            frame=combined.loc[(combined.date>=pd.Timestamp(window['start']))&(combined.date<pd.Timestamp(window['end_exclusive']))].reset_index(drop=True)
            expected=pd.date_range(window['start'],window['end_exclusive'],freq='5min',inclusive='left').as_unit('ns')
            if not pd.DatetimeIndex(frame.date).equals(expected):raise ValueError('Incomplete merged window')
            directory=target/window['id'];directory.mkdir(exist_ok=True)
            path=directory/f'{symbol}-5m-mark.feather';frame.to_feather(path)
            pair=symbol[:-4]+'/USDT:USDT';mark_record=old_plans['futures_'+window['id']]['marks'][pair]
            oldpath=Path(mark_record['path']);oldpath=oldpath if oldpath.is_absolute() else source.ROOT/oldpath
            body=oldpath.read_bytes();digest=source.sha(body)
            if digest!=mark_record['sha256']:raise ValueError('Old hourly mark input changed')
            INPUTS[str(oldpath.relative_to(source.ROOT))]=digest
            old=pd.read_feather(oldpath).set_index('date')['open']
            hourly=frame.set_index('date').loc[lambda x:x.index.minute==0,'open']
            old=old.reindex(hourly.index)
            if old.isna().any():raise ValueError('Missing frozen hourly mark comparison')
            delta=(hourly-old).abs()
            records.append({'symbol':symbol,'pair':pair,'window':window['id'],'path':str(path.relative_to(source.ROOT)),
                'sha256':source.sha(path.read_bytes()),'rows':len(frame),'grid_complete':True,
                'archive_rows':int((frame.source_id<=1000).sum()),'API_gap_rows':int((frame.source_id>1000).sum()),
                'old_hourly_reference':mark_record,'hourly_rows_compared':len(hourly),
                'max_hourly_open_difference':float(delta.max()),'hourly_differences_above_1e_8':int((delta>1e-8).sum()),
                'source_ids':[int(i) for i in frame.source_id.unique()],
                'historical_received_at_certified':False})
        print(json.dumps({'symbol':symbol,'derived_files_completed':len(records)}),flush=True)
    source.save_new(recovery/'assembly_result.json',{'schema_version':1,'state':'complete_official_mark_coverage',
        'derived_files':records,'source_map':origins,
        'input_receipts':[{'path':k,'sha256':v} for k,v in sorted(INPUTS.items())],
        'assembler_sha256':source.sha(Path(__file__).read_bytes()),'grid_complete_files':len(records),
        'official_archives':1000,'official_API_gap_requests':80,'API_gap_rows':sum(r['API_gap_rows'] for r in records),
        'all_frozen_hourly_opens_equal_within_1e_8':all(r['hourly_differences_above_1e_8']==0 for r in records),
        'new_financial_runs':0,'original_P1_inputs_or_results_modified':False,
        'interpolated_prices':0,'orders_enabled':False,'economic_qualified':False,
        'historical_received_at_certified':False,'funding_fill_ordering_certified':False,
        'executable_bid_ask_certified':False})


if __name__=='__main__':main()
