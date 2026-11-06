"""Verify existing futures candle/mark inputs; no financial run or new data."""
import hashlib
import json
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
official=json.loads((ROOT/'research/crypto/official_factor_20261106/plan.json').read_text())
mark_path=ROOT/'research/crypto/rd_program_20261105/data_extension/mark_data_audit.json'
marks=json.loads(mark_path.read_text());records=[];datasets=[]
for window,start,end in [('L','2025-01-01','2026-09-01'),('S','2026-10-23','2026-11-05')]:
    row=next(r for r in official['runs'] if r['market']=='futures' and r['window']==window)
    receipt=ROOT/row['data_receipt']['path']
    if sha(receipt)!=row['data_receipt']['sha256']:raise ValueError('Receipt changed')
    hashes=json.loads(receipt.read_text())
    for name,digest in hashes.items():
        if sha(ROOT/row['datadir']/name)!=digest:raise ValueError('Candle changed: '+name)
    start=pd.Timestamp(start,tz='UTC');end=pd.Timestamp(end,tz='UTC')
    expected=pd.date_range(start,end,freq='5min',inclusive='left').as_unit('ns')
    for pair in official['markets']['futures']['pairs']:
        record=next(r for r in marks['derived_files'] if r['window']==window and r['pair']==pair)
        path=ROOT/record['path']
        if sha(path)!=record['sha256']:raise ValueError('Mark source changed')
        frame=pd.read_feather(path)
        # Normalize representation only; preserve exact timestamps and values.
        if not pd.DatetimeIndex(frame.date).as_unit('ns').equals(expected):raise ValueError('Incomplete mark grid')
        if not np.isfinite(frame.open).all() or not (frame.open>0).all():raise ValueError('Invalid marks')
        stem=pair.replace('/','_').replace(':','_')
        hp=ROOT/row['datadir']/'futures'/f'{stem}-1h-futures.feather'
        hourly=pd.read_feather(hp).set_index('date')
        need=pd.date_range(start-pd.Timedelta(hours=721),end,freq='h',inclusive='left')
        value=hourly.reindex(need).close
        if not np.isfinite(value).all() or not (value>0).all():raise ValueError('Incomplete factor/correlation input')
        records.append(dict(pair=pair,window=window,mark_path=record['path'],mark_sha256=record['sha256'],mark_rows=len(frame),
            hourly_path=str(hp.relative_to(ROOT)),hourly_sha256=sha(hp),required_closed_hours=len(value)))
    datasets.append(dict(window=window,receipt=str(receipt.relative_to(ROOT)),receipt_sha256=sha(receipt),verified_files=len(hashes)))
metadata=ROOT/'research/crypto/strategy_comparison_wide_20261105/public-market-rules-40.json'
result=dict(observed_at_utc=datetime.now(timezone.utc).isoformat(),scope='read-only preflight for subsequent frozen futures cost runs',
    datasets=datasets,mark_files_verified=len(records),records=records,market_metadata_path=str(metadata.relative_to(ROOT)),
    market_metadata_sha256=sha(metadata),mark_audit_sha256=sha(mark_path),source_sha256=sha(Path(__file__)),
    complete_model_price_grids=True,futures_new_financial_runs=0,futures_new_model_integration_verified=False,
    first_receipt_verified=False,historical_rules_verified=False,funding_fill_event_order_verified=False,
    execution_capacity_verified=False,orders_enabled=False)
with (HERE/'futures_data_readiness.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print('Verified datasets:',[(r['window'],r['verified_files']) for r in datasets],'; complete mark grids:',len(records))
