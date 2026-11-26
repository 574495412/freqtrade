"""Official hourly aggressor-flow fields; no factor/return/account selection."""
import ast
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/taker_flow_native_20261114_v1'
PARENT=ROOT/'research/crypto/funding_cross_section_native_20261114_v1/input_admission.json'


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def capture(task):
    symbol,month=task;name=f'{symbol}USDT-1h-{month}.zip';path=OUT/'raw'/name
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}USDT/1h/{name}'
    checksum=path.with_suffix('.CHECKSUM');reused=None
    if not checksum.exists():
        r=requests.get(url+'.CHECKSUM',timeout=30);r.raise_for_status();checksum.write_bytes(r.content)
    expected=checksum.read_text().split()[0]
    if not path.exists():
        source=REUSE/name
        if source.exists() and sha(source)==expected:os.link(source,path);reused=str(source)
        else:
            r=requests.get(url,timeout=30);r.raise_for_status();path.write_bytes(r.content)
    if sha(path)!=expected:raise ValueError('Official ZIP checksum mismatch')
    with ZipFile(path) as archive:frame=pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
    required=['open_time','open','high','low','close','volume','quote_volume','count','taker_buy_volume','taker_buy_quote_volume']
    if not set(required).issubset(frame.columns):raise ValueError('Unsupported official hourly schema: '+str(frame.columns.tolist()))
    frame['date']=pd.to_datetime(frame.open_time,unit='ms',utc=True)
    frame=frame.rename(columns={'volume':'base_volume','count':'number_of_trades','taker_buy_volume':'taker_buy_base_volume'})
    return task,frame,dict(url=url,path=str(path),sha256=expected,checksum_sha256=sha(checksum),reused_identical_source=reused)


def main():
    if (HERE/'input_admission.json').exists():raise FileExistsError('Input admission frozen')
    parent=json.loads(PARENT.read_text());pairs=parent['active_pairs']
    OUT.mkdir(exist_ok=True);(OUT/'raw').mkdir(exist_ok=True)
    global REUSE
    REUSE=ROOT/'user_data/crypto_quant/funding_cross_section_native_20261114_v1/raw/price_1h'
    source=HERE/'source/build_binance_indicator_dataset.py';tree=ast.parse(source.read_text())
    kernel=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_flow_features')
    windows=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FLOW_WINDOWS' for t in n.targets))
    if ast.literal_eval(windows.value)!=[5,20,50,100]:raise ValueError('Pinned flow windows changed')
    # Exact source body extracted with AST, never execute its download/platform main code.
    text='import numpy as np\nimport pandas as pd\n\n'+ast.get_source_segment(source.read_text(),windows)+'\n\n'+ast.get_source_segment(source.read_text(),kernel)+'\n'
    (HERE/'flow_author_kernel.py').write_text(text)
    protocol=dict(frozen_at_utc=datetime.now(UTC).isoformat(),before_new_factor_or_return_outputs=True,
        source_manifest_sha256=sha(HERE/'source_manifest.json'),author_kernel_sha256=sha(HERE/'flow_author_kernel.py'),
        source_role='Exact source feature kernel only; locally defined 1h cross-sectional hypotheses,not author strategy/performance',
        universe='29fixed active source-pool names inherited from prior study; source kernel was built for BTC and supports1h; not PIT allmarket',
        pairs=pairs,features=['cvd_20','taker_imbalance_ma_20','avg_trade_size_z_20'],
        directions=dict(cvd_20='positive aggression continuation',taker_imbalance_ma_20='positive aggression continuation',avg_trade_size_z_20='positive trade-size activity premium hypothesis,not buyer identity'),
        clock='Four-hour decision dates00/04/08/12/16/20 using only completed1h bars; actual native1h signal shifts one bar to01/05/09/13/17/21',
        label='Futures OPEN at decision+1h to decision+5h; four-hour labels,no future bars in features',
        candidate_window='2025-01-01T00:00Z--2026-08-31T23:00Z continuous account domain; last incomplete forward label omitted',
        diagnostics='Actual native IStrategy/DataProvider/isolatedRegistry/factor_analysis. Full/2025/2026 slices,3groups; no account curve substitute',
        positive_gate=dict(ic_mean_gt=.03,ir_gt=.5,positive_ratio_gt=.55),
        scoped_dedup='mean absolute cross-sectional Pearson>.99 duplicate; three new features plus available old carry/flow panels only',
        no_rescue='No source window/sign/timeframe/direction/fee/feature changes after outputs',
        older_A10='spot/perp3day vs30day anomaly remains failed; this local hourly flow family is separate and not a recovery of A10',
        funding_limit='Inherited TIA2026-06-24T04 event UNKNOWN; funded admission not granted by price predictive diagnostics',
        native_accounts_started=False,production_eligible=False)
    save(HERE/'protocol.json',protocol)
    tasks=[(p.split('/')[0],d.strftime('%Y-%m')) for p in pairs for d in pd.date_range('2024-12-01','2026-08-01',freq='MS')]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched=[]
        for result in pool.map(capture,tasks):
            fetched.append(result)
            if len(fetched)%50==0:print('captured',len(fetched),'/',len(tasks),flush=True)
    save(OUT/'public_receipts.json',[r[2] for r in fetched]);records=[];tables=[]
    native_dir=Path(parent['funding_events']['path']).parent/'candles/futures'
    expected=pd.date_range('2024-12-01T00:00Z','2026-09-01T00:00Z',inclusive='left',freq='h')
    for pair in pairs:
        symbol=pair.split('/')[0];frame=pd.concat([r[1] for r in fetched if r[0][0]==symbol],ignore_index=True).sort_values('date')
        if not pd.DatetimeIndex(frame.date).as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Incomplete raw flow calendar: '+symbol)
        columns=['open','high','low','close','base_volume','quote_volume','number_of_trades','taker_buy_base_volume','taker_buy_quote_volume']
        values=frame[columns].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values[:,:4]<=0).any() or (values[:,4:]<0).any():raise ValueError('Invalid source flow fields')
        for buy,total in [('taker_buy_base_volume','base_volume'),('taker_buy_quote_volume','quote_volume')]:
            if frame[buy].gt(frame[total]+np.maximum(1e-9,frame[total]*1e-12)).any():raise ValueError('Impossible taker volume')
        native_path=native_dir/f'{symbol}_USDT_USDT-1h-futures.feather'
        native=pd.read_feather(native_path).set_index('date')
        # Native input startup may begin later than raw warmup. Match every common raw row.
        shared=pd.DatetimeIndex(frame.date).intersection(native.index)
        a=frame.set_index('date').loc[shared,['open','high','low','close','base_volume']].rename(columns={'base_volume':'volume'})
        b=native.loc[shared,['open','high','low','close','volume']]
        errors=(a-b).abs()
        conflict=errors.gt(1e-7).any(axis=1)
        # Preserve differences; never replace the frozen execution tape using a flow outcome.
        records.append(dict(pair=pair,raw_rows=len(frame),matched_native_rows=len(shared),
            native_ohlcv_conflict_rows=int(conflict.sum()),maximum_native_field_error=float(errors.max().max()),
            native_source=str(native_path),native_source_sha256=sha(native_path),
            zero_volume_hours=int(frame.base_volume.eq(0).sum()),zero_trade_hours=int(frame.number_of_trades.eq(0).sum())))
        frame['pair']=pair;tables.append(frame[['date','pair',*columns]])
    target=OUT/'hourly_flow.parquet';pd.concat(tables,ignore_index=True).to_parquet(target,index=False)
    save(HERE/'input_admission.json',dict(passed=all(r['native_ohlcv_conflict_rows']==0 for r in records),
        raw_archives=len(fetched),raw_rows=len(expected)*len(pairs),records=records,
        flow_input=dict(path=str(target),sha256=sha(target)),
        public_receipts=dict(path=str(OUT/'public_receipts.json'),sha256=sha(OUT/'public_receipts.json')),
        original_price_inputs_changed=False,financial_admission=False,private_receipt_clocks_certified=False))
    print('INPUT FINISHED',len(records),'pairs; conflict rows',sum(r['native_ohlcv_conflict_rows'] for r in records),flush=True)


if __name__=='__main__':main()
