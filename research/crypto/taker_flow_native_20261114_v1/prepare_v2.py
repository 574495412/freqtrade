"""Consistent5m execution/flow tape and native1h aggregation, before outcomes.

The official1h-vs-old51row conflict is retained. No original input is changed.
Choose the native detail-timeframe tape, not a source selected for performance.
"""
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from research.crypto.taker_flow_native_20261114_v1.prepare_inputs import HERE,ROOT,OUT,save,sha


def capture(task):
    symbol,month=task;name=f'{symbol}USDT-5m-{month}.zip';path=OUT/'raw5m'/name
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}USDT/5m/{name}'
    checksum=path.with_suffix('.CHECKSUM');reused=None
    if not checksum.exists():
        r=requests.get(url+'.CHECKSUM',timeout=30);r.raise_for_status();checksum.write_bytes(r.content)
    digest=checksum.read_text().split()[0]
    if not path.exists():
        for candidate in REUSE.get(name,[]):
            if sha(candidate)==digest:os.link(candidate,path);reused=str(candidate);break
        if not path.exists():
            r=requests.get(url,timeout=30);r.raise_for_status();path.write_bytes(r.content)
    if sha(path)!=digest:raise ValueError('5m official checksum conflict')
    # Parse each symbol sequentially after capture; do not hold29 full5m frames in memory.
    return task,dict(url=url,path=str(path),sha256=digest,checksum_sha256=sha(checksum),reused_identical_source=reused)


def read_raw(path):
    with ZipFile(path) as archive:frame=pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])),float_precision='round_trip')
    frame['date']=pd.to_datetime(frame.open_time,unit='ms',utc=True)
    return frame.rename(columns={'volume':'base_volume','count':'number_of_trades','taker_buy_volume':'taker_buy_base_volume'})


def main():
    if (HERE/'input_admission_v2.json').exists():raise FileExistsError('V2 admission already frozen')
    prior=json.loads((HERE/'input_admission.json').read_text());protocol=json.loads((HERE/'protocol.json').read_text())
    if prior['passed']:raise ValueError('No demonstrated original admission conflict')
    (OUT/'raw5m').mkdir(exist_ok=True);(OUT/'candles/futures').mkdir(parents=True,exist_ok=True)
    global REUSE
    REUSE={}
    for root in [ROOT/'user_data/crypto_quant/nfi40_data_v002_klines/raw',ROOT/'user_data/crypto_quant/funding_cross_section_native_20261114_v1/raw/price_5m']:
        for path in root.rglob('*-5m-*.zip'):REUSE.setdefault(path.name,[]).append(path)
    save(HERE/'data_contract_v2.json',dict(before_new_factor_or_return_outputs=True,
        reason='51raw1h-vs-oldnative conflicts; choose official5m native-detail tape consistently for both features and execution',
        old_input_admission_sha256=sha(HERE/'input_admission.json'),old_conflicts_sha256=sha(HERE/'price_conflicts.json'),
        protocol_sha256=sha(HERE/'protocol.json'),economic_rule_changed=False,source_kernel_changed=False,
        preserve_old_native_candles=True,choose_matching_old5m_file_only_if_exact_numeric_equality=True,
        no_missing5m_fill=True,aggregation='UTC left-closed1h:open first,high max,low min,close last,all observed volumes/trades SUM',
        initial_price_correction_not_a_funded_retest=True))
    tasks=[(p.split('/')[0],d.strftime('%Y-%m')) for p in protocol['pairs'] for d in pd.date_range('2024-12-01','2026-08-01',freq='MS')]
    fetched=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(capture,tasks):
            fetched.append(result)
            if len(fetched)%50==0:print('captured5m',len(fetched),'/',len(tasks),flush=True)
    save(OUT/'public5m_receipts.json',[r[1] for r in fetched])
    columns=['open','high','low','close','base_volume','quote_volume','number_of_trades','taker_buy_base_volume','taker_buy_quote_volume']
    expected=pd.date_range('2024-12-01T00:00Z','2026-09-01T00:00Z',freq='5min',inclusive='left')
    old_by_pair={r['pair']:r for r in prior['records']}
    tables=[];records=[];daily_checks=[]
    hourly1=pd.read_parquet(prior['flow_input']['path'])
    for pair in protocol['pairs']:
        sym=pair.split('/')[0]
        frame=pd.concat([read_raw(r['path']) for task,r in fetched if task[0]==sym],ignore_index=True).sort_values('date').reset_index(drop=True)
        if not pd.DatetimeIndex(frame.date).as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Missing/duplicate5m source: '+sym)
        if not np.isfinite(frame[columns]).all().all() or frame[['open','high','low','close']].le(0).any().any():raise ValueError('Invalid5m source')
        if frame[['base_volume','quote_volume','number_of_trades','taker_buy_base_volume','taker_buy_quote_volume']].lt(0).any().any():raise ValueError('Negative observed activity')
        for buy,total in [('taker_buy_base_volume','base_volume'),('taker_buy_quote_volume','quote_volume')]:
            if frame[buy].gt(frame[total]+np.maximum(1e-9,frame[total]*1e-12)).any():raise ValueError('Impossible5m taker flow')
        aggregations={c:'sum' for c in columns}
        aggregations.update(open='first',high='max',low='min',close='last')
        hourly=frame.set_index('date')[columns].resample('h',closed='left',label='left').agg(aggregations).reset_index()
        if len(hourly)*12!=len(frame):raise ValueError('Incomplete hourly aggregation')
        hourly['pair']=pair;tables.append(hourly)
        # A matching existing tape can be shared without changing its hash/content.
        source=Path(old_by_pair[pair]['native_source']).with_name(f'{sym}_USDT_USDT-5m-futures.feather')
        old=pd.read_feather(source).set_index('date')
        actual=frame.set_index('date')[['open','high','low','close','base_volume']].rename(columns={'base_volume':'volume'})
        aligned=old.reindex(actual.index)[actual.columns]
        exact=not aligned.isna().any().any() and np.array_equal(actual.to_numpy(),aligned.to_numpy())
        destination=OUT/f'candles/futures/{sym}_USDT_USDT-5m-futures.feather'
        if exact:os.link(source,destination)
        else:actual.reset_index().to_feather(destination)
        price=hourly[['date','open','high','low','close','base_volume']].rename(columns={'base_volume':'volume'})
        price.to_feather(OUT/f'candles/futures/{sym}_USDT_USDT-1h-futures.feather')
        reference=hourly1.loc[hourly1.pair.eq(pair)].set_index('date')[columns]
        errors=(hourly.set_index('date')[columns]-reference).abs()
        differ=errors.gt(1e-7).any(axis=1)
        records.append(dict(pair=pair,raw5m_rows=len(frame),hour_rows=len(hourly),exact_old_5m_numeric_equality=bool(exact),
            old_5m_source=str(source),old_5m_sha256=sha(source),canonical_5m_sha256=sha(destination),
            official_1h_vs_5m_aggregate_conflict_hours=int(differ.sum()),maximum_1h_field_difference=float(errors.max().max())))
        # Prove aggregate conservation with independent scalar sums on every source day.
        dates=frame.date.dt.floor('D')
        for day,part in frame.groupby(dates):
            native=hourly.loc[hourly.date.dt.floor('D').eq(day)]
            for col in ['base_volume','quote_volume','number_of_trades','taker_buy_base_volume','taker_buy_quote_volume']:
                # Different grouping arithmetic may cause tiny absolute rounding at very large volumes.
                left=float(part[col].sum());right=float(native[col].sum())
                if abs(left-right)>max(1e-7,abs(left)*2e-14):raise ValueError('Flow conservation failure')
        print('prepared',sym,flush=True)
    target=OUT/'hourly_flow_v2.parquet';pd.concat(tables,ignore_index=True).to_parquet(target,index=False)
    hashes={str(p.relative_to(OUT/'candles')):sha(p) for p in sorted((OUT/'candles/futures').glob('*.feather'))}
    save(OUT/'data_receipts_v2.json',hashes)
    save(HERE/'input_admission_v2.json',dict(passed=True,flow_input=dict(path=str(target),sha256=sha(target)),
        native_datadir=str(OUT/'candles'),data_receipts=dict(path=str(OUT/'data_receipts_v2.json'),sha256=sha(OUT/'data_receipts_v2.json')),
        records=records,raw_5m_archives=len(fetched),raw_5m_rows=len(expected)*len(protocol['pairs']),
        original_inputs_changed=False,source_kernel_changed=False,economic_rules_changed=False,
        official_1h_disagreements_preserved=True,financial_admission=False,
        funds_and_marks_owner='prior29sourcepool input,including TIAUNKNOWN; not yet bound to funded run'))
    print('V2 PRICE/FLOW INPUT ADMITTED',len(records),'pairs;',len(expected)*len(protocol['pairs']),'raw5m rows',flush=True)


if __name__=='__main__':main()
