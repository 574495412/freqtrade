"""Read-only coverage and receipt validation before long-window financial runs."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
official=json.loads((ROOT/'research/crypto/official_factor_20261106/plan.json').read_text())
row=next(r for r in official['runs'] if r['market']=='spot' and r['window']=='L')
receipt=ROOT/row['data_receipt']['path']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(receipt)==row['data_receipt']['sha256']
hashes=json.loads(receipt.read_text())
for name,digest in hashes.items():
    if sha(ROOT/row['datadir']/name)!=digest:raise ValueError('Changed candle file: '+name)
start=pd.Timestamp('2025-01-01',tz='UTC');end=pd.Timestamp('2026-09-01',tz='UTC')
rows=[]
for pair in official['markets']['spot']['pairs']:
    stem=pair.replace('/','_')
    for interval,freq,warmup in [('5m','5min',pd.Timedelta(hours=169)),('1h','h',pd.Timedelta(hours=721)),('4h','4h',pd.Timedelta(days=90))]:
        path=ROOT/row['datadir']/f'{stem}-{interval}.feather'
        frame=pd.read_feather(path).set_index('date')
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:raise ValueError('Nonunique/unsorted dates')
        expected=pd.date_range(start-warmup,end,freq=freq,inclusive='left')
        selected=frame.reindex(expected)
        prices=selected[['open','high','low','close']].to_numpy()
        if not np.isfinite(prices).all() or (prices<=0).any():raise ValueError('Incomplete/nonpositive price coverage: '+str(path))
        if not np.isfinite(selected.volume).all() or (selected.volume<0).any():raise ValueError('Invalid volume')
        if ((selected.low>selected[['open','close']].min(axis=1)) | (selected.high<selected[['open','close']].max(axis=1))).any():raise ValueError('Invalid OHLC')
        rows.append(dict(pair=pair,timeframe=interval,rows=len(selected),start=str(expected[0]),end=str(expected[-1]),sha256=sha(path)))
result=dict(passed=True,source_receipt_sha256=sha(receipt),receipt_files_checked=len(hashes),target_files_checked=len(rows),
    rows=rows,first_receipt_verified=False,historical_depth_verified=False,historical_rules_verified=False,
    evidence_class='complete_fixed_candle_coverage_only')
with (HERE/'data_admission.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print('Verified',len(hashes),'receipt hashes and',len(rows),'complete required time series')
