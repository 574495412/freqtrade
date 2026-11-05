"""Frozen cash/BTC accounting controls. Offline references, never executable orders."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
PROGRAM = ROOT / 'research/crypto/rd_program_20261105'
HERE = Path(__file__).resolve().parent
LEGACY = {
    ('spot','L'): 'research/crypto/spot_baseline_20261105_v003/plan-long.json',
    ('spot','S'): 'research/crypto/spot_baseline_20261105_v003/plan-short.json',
    ('futures','L'): 'research/crypto/strategy_comparison_wide_20261105/plan.json',
    ('futures','S'): 'research/crypto/strategy_comparison_wide_20261105/plan-post-source.json',
}

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def read(p): return json.loads(Path(p).read_text())
def write_new(p,d):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f: json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False); f.write('\n')

def prepare():
    plan=read(PROGRAM/'plan.json'); registry=read(PROGRAM/'experiment_registry.json')
    for run in registry['runs']:
        if run['baseline_id'] not in ('B0_CASH','B1_BTC_HOLD'): continue
        market=run['market']; window=next(w for w in plan['windows'] if w['id']==run['window_id'])
        spec=plan['markets'][market]; cost=next(c for c in spec['cost_scenarios'] if c['id']==run['cost_id'])
        manifest={'schema_version':1,**run,'registered_at_utc':datetime.now(timezone.utc).isoformat(),
            'freeze_sha256':sha(PROGRAM/'freeze.json'),'source_hashes':{str(Path(__file__).relative_to(ROOT)):sha(__file__)},
            'window':window,'cost_scenario':cost,'risk_profile':'accounting_control','RD1_applied':False,
            'risk_contract_hash':sha(PROGRAM/'plan.json'),'universe_hash':spec['universe_sha256'],
            'initial_equity_usdt':1000,'allocation_fraction':.99,'entry_delay_minutes':5,
            'valuation_endpoint':'end_exclusive minus5minutes; snapshot BEFORE same-time entry/funding events',
            'strategy_main_timeframe':None,'detail_timeframe':'5m','startup_candle_count':0,
            'stake_amount':'99% initial capital inclusive entry fee; round quantity down to archived amount step',
            'order_types':None,'resolved_strategy_config_hash':sha(PROGRAM/'plan.json'),
            'command':f'.venv-paper/bin/python {Path(__file__).relative_to(ROOT)} run --id {run["id"]}',
            'isolated_output_path':f'user_data/crypto_quant/rd_program_20261105_v1/{run["id"]}',
            'candidate_readiness':'accounting_only_no_live_execution_claim','data_receipt_hashes':{},
            'dependencies':{},'orders_enabled':False,'production_eligible':False}
        from importlib.metadata import version
        manifest['dependencies']={k:version(k) for k in ('numpy','pandas','pyarrow')}
        if run['baseline_id']=='B1_BTC_HOLD':
            legacy=read(ROOT/LEGACY[(market,run['window_id'])]); datadir=Path(legacy['datadir'])
            receipts=read(legacy['data_receipts']['path'])
            if sha(legacy['data_receipts']['path'])!=legacy['data_receipts']['sha256']: raise ValueError('Receipt drift')
            suffix='futures/BTC_USDT_USDT-5m-futures.feather' if market=='futures' else 'BTC_USDT-5m.feather'
            paths={'prices':datadir/suffix,'market_rules':Path(legacy['market_snapshot']['path'])}
            if market=='futures':
                paths.update(marks=datadir/'futures/BTC_USDT_USDT-1h-mark.feather',funding=datadir/'futures/BTC_USDT_USDT-1h-funding_rate.feather')
            for name,path in paths.items():
                digest=sha(path)
                expected=legacy['market_snapshot']['sha256'] if name=='market_rules' else receipts[str(path.relative_to(datadir))]
                if digest!=expected: raise ValueError(f'Historical data changed: {name}')
                manifest['data_receipt_hashes'][name]={'path':str(path.relative_to(ROOT)),'sha256':digest}
            manifest['data_receipt_hashes']['parent_receipt']={'path':str(Path(legacy['data_receipts']['path']).relative_to(ROOT)),'sha256':legacy['data_receipts']['sha256']}
            manifest['limitations']=['historically observed data','archived current market precision is not proven point-in-time','OPEN-price matching and fee proxy do not establish executable fills','no depth/queue/impact or liquidation simulation','no new signal; BTC beta accounting reference']
            if market=='futures':manifest['limitations']+=['5m trade OPEN is conditional reference, not verified5m mark; hourly actual marks separately','settled funding is cost only, not a decision feature']
        else:
            manifest['limitations']=['USDT-denominated zero-order control; no interest or USD peg valuation','cash has undefined Sharpe/Sortino/Calmar when denominator zero']
        write_new(HERE/'manifests'/f'{run["id"]}.json',manifest)
    print('prepared16 manifests; no account evaluated')

def account_curve(index, prices, fee, step, market='spot', funding=None, marks=None):
    """Pre-event snapshots; last frozen-price snapshot reserves hypothetical exit cost."""
    import numpy as np
    import pandas as pd
    capital=1000.; entry_time=index[0]+pd.Timedelta(minutes=5)
    if not prices.index.equals(index): raise ValueError('Incomplete5m calendar')
    if not np.isfinite(prices).all() or (prices<=0).any(): raise ValueError('Invalid prices')
    entry_price=float(prices.loc[entry_time]); budget=Decimal('990')
    quantity=(budget/(Decimal(str(entry_price))*(1+Decimal(str(fee))))/Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(step))
    qty=float(quantity); entry_value=qty*entry_price; entry_fee=entry_value*fee
    if qty<=0: raise ValueError('No valid quantity')
    fund_flows=pd.Series(0.,index=index)
    events=[]
    if market=='futures':
        if funding is None or marks is None: raise ValueError('Missing actual funding/mark inputs')
        if funding.index.has_duplicates or not funding.index.is_monotonic_increasing: raise ValueError('Ambiguous funding chronology')
        for t,rate in funding.items():
            if not entry_time<t<index[-1]: continue
            if t not in marks.index or not np.isfinite(rate) or not np.isfinite(marks.loc[t]) or marks.loc[t]<=0: raise ValueError('Missing mark at funding event')
            flow=-qty*float(marks.loc[t])*float(rate)
            pos=index.searchsorted(t,side='right')
            if pos<len(index): fund_flows.iloc[pos]+=flow
            events.append({'time':str(t),'rate':float(rate),'mark':float(marks.loc[t]),'cash_flow':flow})
    cumulative_funding=fund_flows.cumsum()
    invested=index>entry_time
    if market=='spot':
        cash=capital-entry_value-entry_fee
        nav=cash+qty*prices*(1-fee)
    else:
        nav=capital-entry_fee+qty*(prices-entry_price)-qty*prices*fee+cumulative_funding
    nav.loc[~invested]=capital
    curve=pd.DataFrame({'date':index,'reference_equity':nav.to_numpy(),'funding_cash':cumulative_funding.to_numpy(),'gross_notional':np.where(invested,qty*prices,0)})
    meta={'quantity':qty,'entry_time':str(entry_time),'entry_price':entry_price,'entry_fee':entry_fee,'entry_notional':entry_value,'funding_events':events,'funding_net_usdt':float(cumulative_funding.iloc[-1]),'natural_complete_campaigns':0,'terminal_open_positions':1,'terminal_liquidation_is_hypothetical':True}
    if market=='futures':
        hours=marks.loc[(marks.index>=index[0])&(marks.index<=index[-1])]
        expected=pd.date_range(index[0],index[-1].floor('h'),freq='h')
        if not hours.index.equals(expected): raise ValueError('Missing hourly actual marks')
        marknav=capital-entry_fee+qty*(hours-entry_price)-qty*hours*fee+cumulative_funding.reindex(hours.index)
        marknav.loc[marknav.index<=entry_time]=capital
        curve['hourly_mark_equity']=curve.date.map(marknav)
        meta['hourly_mark_end_time']=str(hours.index[-1]);meta['hourly_mark_end_equity']=float(marknav.iloc[-1]);meta['hourly_mark_mdd']=float((1-marknav/marknav.cummax()).max())
    return curve,meta

def metrics(curve):
    import numpy as np
    x=curve.set_index('date').reference_equity
    # Use exact UTC00 snapshots for full days; no partial last day in daily ratios.
    daily=x.loc[(x.index.hour==0)&(x.index.minute==0)].pct_change().dropna()
    sd=daily.std(ddof=1); downside=np.sqrt(np.mean(np.minimum(daily,0)**2)) if len(daily) else 0
    dd=1-x/x.cummax(); threshold=daily.quantile(.05) if len(daily) else 0
    return {'net_reference_return':float(x.iloc[-1]/1000-1),'reference_mdd':float(dd.max()),'last_reference_equity':float(x.iloc[-1]),'daily_NAV_Sharpe':float(daily.mean()/sd*np.sqrt(365)) if sd>0 else None,'daily_Sortino':float(daily.mean()/downside*np.sqrt(365)) if downside>0 else None,'worst_complete_day':float(daily.min()) if len(daily) else None,'daily_ES95_return':float(daily[daily<=threshold].mean()) if len(daily) else None,'calendar_snapshots':len(x),'complete_daily_returns':len(daily),'initial_floor_breached':bool((x<=500).any())}

def _run(run_id):
    import numpy as np
    import pandas as pd
    from importlib.metadata import version
    manifest_path=HERE/'manifests'/f'{run_id}.json'; m=read(manifest_path)
    if sha(PROGRAM/'freeze.json')!=m['freeze_sha256']: raise ValueError('Plan freeze drift')
    for relative,digest in m['source_hashes'].items():
        if sha(ROOT/relative)!=digest: raise ValueError('Runner source drift')
    for name,v in m['dependencies'].items():
        if version(name)!=v: raise ValueError('Dependency drift')
    for rec in m['data_receipt_hashes'].values():
        if sha(ROOT/rec['path'])!=rec['sha256']:raise ValueError('Data drift')
    out=ROOT/m['isolated_output_path'];out.mkdir(parents=True,exist_ok=False)
    write_new(out/'run_manifest.json',{**m,'manifest_sha256':sha(manifest_path),'start_utc':datetime.now(timezone.utc).isoformat()})
    index=pd.date_range(m['window']['start'],pd.Timestamp(m['window']['end_exclusive'])-pd.Timedelta(minutes=5),freq='5min')
    if m['baseline_id']=='B0_CASH':
        curve=pd.DataFrame({'date':index,'reference_equity':np.full(len(index),1000.),'gross_notional':np.zeros(len(index)),'funding_cash':np.zeros(len(index))})
        meta={'quantity':0,'entry_fee':0,'entry_notional':0,'funding_net_usdt':0,'natural_complete_campaigns':0,'terminal_open_positions':0,'funding_events':[]}
    else:
        def load(name,col): return pd.read_feather(ROOT/m['data_receipt_hashes'][name]['path']).set_index('date')[col]
        price=load('prices','open').loc[index[0]:index[-1]]
        rules=read(ROOT/m['data_receipt_hashes']['market_rules']['path']); pair='BTC/USDT' if m['market']=='spot' else 'BTC/USDT:USDT'; market=rules['markets'][pair]
        curve,meta=account_curve(index,price,m['cost_scenario']['total_bps_per_side']/10000,market['precision']['amount'],m['market'],load('funding','funding_rate') if m['market']=='futures' else None,load('marks','open') if m['market']=='futures' else None)
        minimum=market['limits']['cost'].get('min') or 0
        if meta['entry_notional']<minimum:raise ValueError('Below archived min notional')
    curve.to_parquet(out/'reference_equity.parquet',index=False)
    events=meta.pop('funding_events');write_new(out/'funding_events.json',events)
    result={'id':run_id,'state':'accounting_reference_completed','metrics':metrics(curve),**meta,'manifest_sha256':sha(manifest_path),'output_sha256':sha(out/'reference_equity.parquet'),'reference_valuation':'constant USDT cash' if m['baseline_id']=='B0_CASH' else '5m spot OPEN' if m['market']=='spot' else 'conditional5m perpetual trade OPEN; actual hourlymark separate','economic_acceptance':'not_qualified_by_accounting_control','verified_5m_futures_mark':False,'RD1_applied':False,'orders_enabled':False,'production_eligible':False,'limitations':m['limitations'],'end_utc':datetime.now(timezone.utc).isoformat()}
    write_new(out/'result.json',result)
    ledger=HERE/'execution_ledger.jsonl'
    with ledger.open('a') as f:f.write(json.dumps({'id':run_id,'state':result['state'],'manifest_sha256':sha(manifest_path),'result_path':str((out/'result.json').relative_to(ROOT)),'result_sha256':sha(out/'result.json'),'start_utc':read(out/'run_manifest.json')['start_utc'],'end_utc':result['end_utc']},ensure_ascii=False)+'\n')
    print(json.dumps({'id':run_id,'state':result['state'],'net_reference_return':result['metrics']['net_reference_return']}))

def run(run_id):
    started=datetime.now(timezone.utc).isoformat()
    try:
        return _run(run_id)
    except Exception as exc:
        failure={'id':run_id,'state':'failed','start_utc':started,'end_utc':datetime.now(timezone.utc).isoformat(),'type':type(exc).__name__,'message':str(exc),'orders_enabled':False}
        with (HERE/'technical_failures.jsonl').open('a') as f:f.write(json.dumps(failure,ensure_ascii=False)+'\n')
        manifest_path=HERE/'manifests'/f'{run_id}.json'
        if manifest_path.exists():
            out=ROOT/read(manifest_path)['isolated_output_path']
            if out.exists() and not (out/'failure.json').exists() and not (out/'result.json').exists():write_new(out/'failure.json',failure)
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','run']);p.add_argument('--id');a=p.parse_args()
    if a.mode=='prepare':prepare()
    else:
        if not a.id:raise SystemExit('--id required')
        run(a.id)
