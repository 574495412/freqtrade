"""Reconstruct native fills and funding; gate 5m output on exact hourly mark parity.

Read-only inputs and independent accounting. This does not import strategies,
start backtests, or use credentials. The 5m price is futures candle OPEN, not a
mark, an executable fill, or a guarantee of intrabar risk.
"""
import hashlib
import json
import math
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

CZ = Path('/Users/chrise/GitHub/CZ-Agent')
FT = Path('/Users/chrise/GitHub/freqtrade')
ROOT = FT / 'user_data/crypto_quant'
RUNS = [ROOT / 'wide_strategy_comparison_20261105_v001',
        ROOT / 'wide_strategy_cost15_20261105_v001',
        ROOT / 'wide_strategy_x7_20261105_v001']
MANIFEST = CZ / 'vendor/FREQTRADE_SOURCE.json'
OUT = ROOT / 'funding_reconstructed_5m_reference_20261105_v002'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def ms(series):
    return series.astype('datetime64[ms, UTC]').astype('int64').to_numpy()

def load_accounts():
    plans = [json.loads((run / 'registered_plan.json').read_text()) for run in RUNS]
    shared = ('engine_commit','start','end','comparison_end','pairs','initial_equity_usdt',
              'datadir','data_receipts','marks','market_snapshot','periods')
    for key in shared:
        if any(p[key] != plans[0][key] for p in plans):
            raise ValueError('Frozen account contract differs: ' + key)
    manifest = json.loads(MANIFEST.read_text())
    if manifest['commit'] != plans[0]['engine_commit']:
        raise ValueError('Wrong engine identity')
    for name in ('freqtrade/optimize/backtesting.py','freqtrade/exchange/exchange.py','freqtrade/persistence/trade_model.py'):
        if sha(CZ / 'vendor/freqtrade' / name) != manifest['files'][name]:
            raise ValueError('Engine source changed: ' + name)
    receipt = Path(plans[0]['data_receipts']['path'])
    if sha(receipt) != plans[0]['data_receipts']['sha256']:
        raise ValueError('Data receipt changed')
    receipts = json.loads(receipt.read_text())
    market = Path(plans[0]['datadir'])
    accounts = []
    for run in RUNS:
        if not (run / 'completion.json').exists():
            raise ValueError('Account matrix incomplete: ' + str(run))
        for result in sorted(run.glob('*/result.json')):
            folder = result.parent
            if json.loads((folder / 'registered_plan.json').read_text()) != plans[RUNS.index(run)]:
                raise ValueError('Account plan changed: ' + str(folder))
            audit = json.loads((folder / 'independent-account-audit.json').read_text())
            archive, = (folder / 'native_results').glob('*.zip')
            for path, key in ((run / 'registered_plan.json','plan_sha256'),(archive,'archive_sha256'),
                              (result,'result_sha256'),(folder / 'hourly_equity.parquet','curve_sha256')):
                if sha(path) != audit[key]:
                    raise ValueError('Audit hash differs: ' + str(path))
            with zipfile.ZipFile(archive) as z:
                filename, = [n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                stats = next(iter(json.loads(z.read(filename))['strategy'].values()))
            accounts.append({'name':folder.name,'folder':folder,'stats':stats,'result_sha256':sha(result),
                             'archive_sha256':sha(archive),'audit_sha256':sha(folder / 'independent-account-audit.json')})
    if len(accounts) != 6:
        raise ValueError('Expected six accounts')
    return plans[0], market, receipts, accounts, sha(receipt)

def verified_frame(market, receipts, pair, suffix):
    stem = pair.replace('/','_').replace(':','_')
    relative = f'futures/{stem}-{suffix}.feather'
    path = market / relative
    if sha(path) != receipts[relative]:
        raise ValueError('Frozen input differs: ' + relative)
    frame = pd.read_feather(path)
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError('Unsorted or duplicate timestamps: ' + relative)
    return frame

def funding_events(trade, funding, mark):
    df = funding.merge(mark[['date','open']], on='date', how='inner', validate='one_to_one')
    start = pd.to_datetime(trade['open_timestamp'],unit='ms',utc=True)
    end = pd.to_datetime(trade['close_timestamp'],unit='ms',utc=True)
    df = df[(df.date >= start) & (df.date <= end)]
    orders = sorted(trade['orders'], key=lambda o:o['order_filled_timestamp'])
    ticks = np.array([o['order_filled_timestamp'] for o in orders],dtype='int64')
    dq = np.array([o['amount']*(1 if o['ft_is_entry'] else -1) for o in orders])
    qty = np.r_[0,np.cumsum(dq)]
    event_ms = ms(df.date)
    left = np.searchsorted(ticks,event_ms,'left')
    right = np.searchsorted(ticks,event_ms,'right')
    rates = df.funding_rate.to_numpy()*df.open.to_numpy()*(1 if trade['is_short'] else -1)
    # A same-time fill first stores the funding accrued before the fill, then
    # recalculates the trade after the force-update. That recalculation drops
    # the new running component from observable funding_fees. It reappears at
    # the next hourly funding update or next filled order, whichever is first.
    values = rates*qty[left]
    delayed_times, delayed_values = [], []
    for i in np.nonzero(left != right)[0]:
        next_hour = (int(event_ms[i])//3600000 + 1)*3600000
        next_order = int(ticks[right[i]]) if right[i] < len(ticks) else next_hour
        activate = min(next_hour,next_order)
        delayed_times.append(activate)
        delayed_values.append(rates[i]*math.fsum(float(qty[j+1]) for j in range(left[i],right[i])))
    event_ms = np.r_[event_ms,np.array(delayed_times,dtype='int64')]
    values = np.r_[values,np.array(delayed_values,dtype='float64')]
    order = np.argsort(event_ms,kind='stable')
    event_ms, values = event_ms[order],values[order]
    if abs(float(np.sum(values))-trade['funding_fees']) > 1e-7:
        raise ValueError('Native funding mismatch: '+trade['pair']+' '+trade['open_date'])
    return event_ms, np.r_[0,np.cumsum(values)]

def contribute(trade, valuation_ms, price, funding, mark, output):
    open_ms, close_ms = trade['open_timestamp'], trade['close_timestamp']
    after_close = np.searchsorted(valuation_ms,close_ms,'right')
    output[after_close:] += trade['profit_abs']
    left = np.searchsorted(valuation_ms,open_ms,'right')
    right = np.searchsorted(valuation_ms,close_ms,'right')
    if left >= right:
        return
    times = valuation_ms[left:right]
    orders = sorted(trade['orders'], key=lambda o:o['order_filled_timestamp'])
    order_ms = np.array([o['order_filled_timestamp'] for o in orders],dtype='int64')
    n = np.searchsorted(order_ms,times,'left')  # observations before same-bar processing
    qty_delta = np.array([o['amount']*(1 if o['ft_is_entry'] else -1) for o in orders])
    entry_delta = np.array([o['amount']*o['safe_price'] if o['ft_is_entry'] else 0 for o in orders])
    exit_delta = np.array([0 if o['ft_is_entry'] else o['amount']*o['safe_price'] for o in orders])
    quantity = np.r_[0,np.cumsum(qty_delta)][n]
    entry = np.r_[0,np.cumsum(entry_delta)][n]
    exit_ = np.r_[0,np.cumsum(exit_delta)][n]
    if np.any(quantity < -1e-7):
        raise ValueError('Negative position: '+trade['pair'])
    fms, fcum = funding_events(trade,funding,mark)
    accrued = fcum[np.searchsorted(fms,times,'left')]
    p = price[left:right]
    if not np.isfinite(p).all() or (p <= 0).any():
        raise ValueError('Missing valuation price: '+trade['pair'])
    side = -1 if trade['is_short'] else 1
    output[left:right] += (side*(exit_+quantity*p-entry) - entry*trade['fee_open']
                           - exit_*trade['fee_close'] - quantity*p*trade['fee_close'] + accrued)

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    try:
        plan,market,receipts,accounts,receipt_sha = load_accounts()
        hourly = pd.date_range(plan['start'],plan['comparison_end'],freq='h')
        t_hour = ms(hourly.to_series(index=range(len(hourly))))
        hvals = {a['name']:np.full(len(hourly),plan['initial_equity_usdt'],dtype='float64') for a in accounts}
        pairs = sorted({t['pair'] for a in accounts for t in a['stats']['trades']})
        if not set(pairs) <= set(plan['pairs']):
            raise ValueError('Trade outside registered universe')
        for pair in pairs:
            mark=verified_frame(market,receipts,pair,'1h-mark')
            fund=verified_frame(market,receipts,pair,'1h-funding_rate')
            series=mark.set_index('date')['open'].reindex(hourly)
            prices=series.to_numpy(dtype='float64')
            for a in accounts:
                for t in a['stats']['trades']:
                    if t['pair']==pair:
                        contribute(t,t_hour,prices,fund,mark,hvals[a['name']])
        audits=[]
        for a in accounts:
            curve=pd.read_parquet(a['folder']/'hourly_equity.parquet').set_index('date')
            observed=curve['hourly_mark_equity'].reindex(hourly)
            if observed.isna().any() or not observed.index.equals(hourly):
                raise ValueError('Missing hour in archived curve: '+a['name'])
            errors=hvals[a['name']]-observed.to_numpy(dtype='float64')
            worst=int(np.argmax(np.abs(errors)))
            check={'account':a['name'],'max_abs_error_usdt':float(abs(errors[worst])),
                   'worst_hour_utc':hourly[worst].isoformat(),'reconstructed':float(hvals[a['name']][worst]),
                   'archived':float(observed.iloc[worst]),'hours':len(hourly)}
            audits.append(check)
        (OUT/'hourly-parity-attempt.json').write_text(json.dumps({'accounts':audits,'threshold_usdt':1e-5},indent=2)+'\n')
        if any(a['max_abs_error_usdt'] > 1e-5 for a in audits):
            raise ValueError('Hourly full-account parity failed; 5m output prohibited')
        five=pd.date_range(plan['start'],plan['comparison_end'],freq='5min')
        t_five=ms(five.to_series(index=range(len(five))))
        vals={a['name']:np.full(len(five),plan['initial_equity_usdt'],dtype='float64') for a in accounts}
        for pair in pairs:
            mark=verified_frame(market,receipts,pair,'1h-mark')
            fund=verified_frame(market,receipts,pair,'1h-funding_rate')
            frame=verified_frame(market,receipts,pair,'5m-futures')
            prices=frame.set_index('date')['open'].reindex(five).to_numpy(dtype='float64')
            for a in accounts:
                for t in a['stats']['trades']:
                    if t['pair']==pair:
                        contribute(t,t_five,prices,fund,mark,vals[a['name']])
        records=[]
        for a in accounts:
            v=vals[a['name']]
            if not np.isfinite(v).all():
                raise ValueError('Nonfinite 5m equity: '+a['name'])
            peak=np.maximum.accumulate(v)
            dd=1-v/peak
            path=OUT/(a['name']+'-five-minute-reference.parquet')
            pd.DataFrame({'date':five,'equity_5m_futures_open_reference':v,'drawdown_5m_futures_open_reference':dd}).to_parquet(path,index=False)
            records.append({'account':a['name'],'curve':str(path),'sha256':sha(path),
                            'max_5m_futures_open_reference_drawdown':float(dd.max()),
                            'end_equity_usdt':float(v[-1]),'archive_sha256':a['archive_sha256'],
                            'result_sha256':a['result_sha256'],'audit_sha256':a['audit_sha256']})
        (OUT/'completion.json').write_text(json.dumps({'schema':'cz.reconstructed-five-minute-futures-open.v1',
          'generated_utc':datetime.now(timezone.utc).isoformat(),'hourly_mark_parity':audits,
          'data_receipts_sha256':receipt_sha,'accounts':records,'funding':'reconstructed pinned engine model; no account settlement',
          'valuation':'5m futures candle open before same-bar processing, with fees and prospective close-fee reserve; not 5m mark, executable quote, or intrabar worst case',
          'new_financial_backtests':0,'real_orders':False},indent=2)+'\n')
        print(json.dumps({'status':'complete','accounts':records},indent=2))
    except Exception as exc:
        (OUT/'failure.json').write_text(json.dumps({'type':type(exc).__name__,'message':str(exc),
                                                   'traceback':traceback.format_exc(),'five_minute_output_authorized':False},indent=2)+'\n')
        raise

if __name__ == '__main__':
    main()
