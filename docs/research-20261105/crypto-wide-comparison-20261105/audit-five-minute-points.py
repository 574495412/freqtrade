"""Independently check selected 5m curve points directly from archived fills.

Different scalar loop implementation from the vector reconstruction. It makes
no order or financial backtest and does not replace the original account.
"""
import hashlib
import json
import math
import zipfile
from pathlib import Path

import pandas as pd

FT = Path('/Users/chrise/GitHub/freqtrade')
ROOT = FT / 'user_data/crypto_quant'
CURVES = ROOT / 'funding_reconstructed_5m_reference_20261105_v002'
OUT = CURVES / 'independent-point-audit.json'
RUNS = [ROOT/'wide_strategy_comparison_20261105_v001',ROOT/'wide_strategy_cost15_20261105_v001',ROOT/'wide_strategy_x7_20261105_v001']
POINTS = ['2025-01-01T00:00:00Z','2025-02-03T02:10:00Z','2025-08-10T16:05:00Z',
          '2025-08-10T17:05:00Z','2026-08-23T08:05:00Z','2026-08-23T09:05:00Z',
          '2026-08-31T23:00:00Z']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if OUT.exists():
        raise ValueError('Refuse to overwrite independent point audit')
    completion_path=CURVES/'completion.json'
    completion=json.loads(completion_path.read_text())
    plan=json.loads((RUNS[0]/'registered_plan.json').read_text())
    receipt=Path(plan['data_receipts']['path'])
    if sha(receipt)!=plan['data_receipts']['sha256']:
        raise ValueError('Changed data receipt')
    hashes=json.loads(receipt.read_text())
    market=Path(plan['datadir'])
    cache={}
    def load(pair):
        if pair not in cache:
            stem=pair.replace('/','_').replace(':','_')
            frames=[]
            for kind in ('5m-futures','1h-funding_rate','1h-mark'):
                rel=f'futures/{stem}-{kind}.feather'; file=market/rel
                if sha(file)!=hashes[rel]:raise ValueError('Data mismatch '+rel)
                frames.append(pd.read_feather(file).set_index('date'))
            cache[pair]=frames
        return cache[pair]
    answers=[]
    for run in RUNS:
        for result in sorted(run.glob('*/result.json')):
            account=result.parent
            archive,=(account/'native_results').glob('*.zip')
            prior=json.loads((account/'independent-account-audit.json').read_text())
            if sha(archive)!=prior['archive_sha256'] or sha(result)!=prior['result_sha256']:
                raise ValueError('Changed archive or result')
            with zipfile.ZipFile(archive) as z:
                fn,=[n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                trades=next(iter(json.loads(z.read(fn))['strategy'].values()))['trades']
            record=next(a for a in completion['accounts'] if a['account']==account.name)
            curvefile=Path(record['curve'])
            if sha(curvefile)!=record['sha256']:
                raise ValueError('Changed reconstructed curve')
            curve=pd.read_parquet(curvefile).set_index('date')
            discrepancies=[]
            for pt in POINTS:
                at=pd.Timestamp(pt); at_ms=int(at.timestamp()*1000)
                equity=1000.0
                for trade in trades:
                    if trade['close_timestamp'] < at_ms:
                        equity+=trade['profit_abs'];continue
                    if trade['open_timestamp'] >= at_ms:
                        continue
                    prices,fund,marks=load(trade['pair'])
                    price=float(prices.loc[at,'open'])
                    orders=sorted(trade['orders'],key=lambda o:o['order_filled_timestamp'])
                    filled=[o for o in orders if o['order_filled_timestamp']<at_ms]
                    entries=math.fsum(o['amount']*o['safe_price'] for o in filled if o['ft_is_entry'])
                    exits=math.fsum(o['amount']*o['safe_price'] for o in filled if not o['ft_is_entry'])
                    qty=math.fsum(o['amount']*(1 if o['ft_is_entry'] else -1) for o in filled)
                    sign=-1 if trade['is_short'] else 1
                    contribution=sign*(exits+qty*price-entries)-entries*trade['fee_open']-exits*trade['fee_close']-qty*price*trade['fee_close']
                    begin=pd.to_datetime(trade['open_timestamp'],unit='ms',utc=True)
                    for when, row in fund.loc[(fund.index>=begin)&(fund.index<at)].iterrows():
                        if when not in marks.index:
                            raise ValueError('Missing mark at funding event '+trade['pair']+' '+str(when))
                        event_ms=int(when.timestamp()*1000)
                        pre=math.fsum(o['amount']*(1 if o['ft_is_entry'] else -1)
                                      for o in orders if o['order_filled_timestamp']<event_ms)
                        simultaneous=[o for o in orders if o['order_filled_timestamp']==event_ms]
                        unit=float(row['funding_rate'])*float(marks.loc[when,'open'])*(-1 if not trade['is_short'] else 1)
                        contribution+=unit*pre
                        current=pre
                        future_fills=[o['order_filled_timestamp'] for o in orders if o['order_filled_timestamp']>event_ms]
                        next_hour=(event_ms//3600000+1)*3600000
                        trigger=min([next_hour,*future_fills])
                        for o in simultaneous:
                            current+=o['amount']*(1 if o['ft_is_entry'] else -1)
                            if trigger<at_ms:
                                contribution+=unit*current
                    equity+=contribution
                expected=float(curve.loc[at,'equity_5m_futures_open_reference'])
                discrepancies.append({'time_utc':at.isoformat(),'independent_equity':equity,'curve_equity':expected,'difference_usdt':equity-expected})
            max_error=max(abs(x['difference_usdt']) for x in discrepancies)
            answers.append({'account':account.name,'max_abs_error_usdt':max_error,'points':discrepancies})
            if max_error>1e-5:raise ValueError('Point reconciliation failed: '+account.name)
    if len(answers)!=6:raise ValueError('Expected 6 accounts')
    OUT.write_text(json.dumps({'schema':'cz.five-minute-reference-independent-point-audit.v1',
                               'scope':'7 selected points per account, not full 5m exhaustive independent verification',
                               'completion_sha256':sha(completion_path),'accounts':answers,
                               'new_financial_accounts':0,'orders_enabled':False},indent=2)+'\n')
    print(json.dumps([{'account':a['account'],'max_abs_error_usdt':a['max_abs_error_usdt']} for a in answers],indent=2))

if __name__=='__main__':main()
