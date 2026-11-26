"""Independent full-pool clock/value audit without simulated trades or returns."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    admission=json.loads((HERE/'input_admission.json').read_text());out=Path(admission['funding_events']['path']).parent
    for row in json.loads(Path(admission['public_receipts']['path']).read_text()):
        if sha(row['path'])!=row['sha256']:raise ValueError('Official ZIP drift')
        checksum=Path(row['path']).with_suffix('.CHECKSUM')
        if checksum.read_text().split()[0]!=row['sha256'] or sha(checksum)!=row['checksum_sha256']:raise ValueError('Official checksum drift')
    data=json.loads(Path(admission['data_receipts']['path']).read_text());records=[]
    for name,digest in data.items():
        if sha(out/'candles'/name)!=digest:raise ValueError('Frozen native input drift')
    funds=pd.read_parquet(admission['funding_events']['path']);gaps=[]
    for pair in admission['active_pairs']:
        sym=pair.split('/')[0]
        for suffix,freq,start in [('1h-futures','h','2025-01-01T00:00Z'),('5m-futures','5min','2025-01-01T00:00Z'),('1h-mark','h','2025-01-01T00:00Z'),('1d-futures','D','2024-10-29T00:00Z')]:
            p=out/f'candles/futures/{sym}_USDT_USDT-{suffix}.feather'
            f=pd.read_feather(p);f=f.loc[f.date.ge(pd.Timestamp(start)) & f.date.lt(pd.Timestamp('2026-09-01T00:00Z'))]
            expected=pd.date_range(start,'2026-09-01T00:00Z',freq=freq,inclusive='left')
            if not pd.DatetimeIndex(f.date).as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Full-pool native calendar missing: '+sym+' '+suffix)
            prices=f[['open','high','low','close']]
            if not np.isfinite(prices).all().all() or prices.le(0).any().any():raise ValueError('Unknown/nonpositive native price')
            if (f.high<f[['open','close','low']].max(axis=1)).any() or (f.low>f[['open','close','high']].min(axis=1)).any():raise ValueError('OHLC range conflict')
            records.append({'pair':pair,'candle':suffix,'rows':len(f),'ohlc_cells':len(f)*4,'calendar_complete':True})
        raw=funds.loc[funds.pair.eq(pair)]
        interval=4 if sym=='TIA' else 8
        expected=pd.date_range('2024-12-01T00:00Z','2026-09-01T00:00Z',freq=f'{interval}h',inclusive='left')
        for missing in expected.difference(pd.DatetimeIndex(raw.date)):gaps.append({'pair':pair,'expected_event':missing.isoformat()})
        if len(pd.DatetimeIndex(raw.date).difference(expected)):raise ValueError('Unexpected funding event grid')
    if gaps!=[{'pair':'TIA/USDT:USDT','expected_event':'2026-06-24T04:00:00+00:00'}]:raise ValueError('Unreviewed missing funding inputs')
    if len(funds)!=28*639*3+639*6-1:raise ValueError('Independent full funding count')
    result={'passed_for_price_inputs':True,'funding_admission':False,'native_source_files':len(data),
        'official_archives_sha_verified':admission['raw_archives'],'raw_funding_events':len(funds),
        'full_input_missing_funding_events':gaps,'price_records':records,
        'price_rows_checked':sum(r['rows'] for r in records),'ohlc_cells_checked':sum(r['ohlc_cells'] for r in records),
        'no_price_or_funding_imputation':True,'native_funded_accounts':0,'first_receipt_timing_certified':False}
    (HERE/'input_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print('FULL POOL INPUT AUDIT',result['price_rows_checked'],'price rows;',len(funds),'funding events; 1 unresolved event retained')


if __name__=='__main__':main()
