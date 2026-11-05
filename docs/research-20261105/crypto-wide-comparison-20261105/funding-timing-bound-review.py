"""Recount a narrow, conditional timing sensitivity from frozen native outputs.

The sum is NOT a proven NAV error bound unless every other accounting term is
correct and the only discrepancy is the timing of these reconstructed terms.
It does not replay the matching engine, estimate execution, or use credentials.
"""
import hashlib
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FT = Path('/Users/chrise/GitHub/freqtrade')
ROOT = FT / 'user_data/crypto_quant'
RUNS = [ROOT / 'wide_strategy_comparison_20261105_v001',
        ROOT / 'wide_strategy_cost15_20261105_v001',
        ROOT / 'wide_strategy_x7_20261105_v001']
OUT = Path(__file__).with_name('funding-timing-bound-review.json')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    plan = json.loads((RUNS[0] / 'registered_plan.json').read_text())
    market = Path(plan['datadir'])
    receipt = Path(plan['data_receipts']['path'])
    if sha(receipt) != plan['data_receipts']['sha256']:
        raise ValueError('Data receipt hash mismatch')
    checksums = json.loads(receipt.read_text())
    cache = {}
    accounts = []
    for run in RUNS:
        current = json.loads((run / 'registered_plan.json').read_text())
        if current['data_receipts'] != plan['data_receipts'] or current['datadir'] != plan['datadir']:
            raise ValueError('Accounts do not share the registered dataset')
        for result in sorted(run.glob('*/result.json')):
            folder = result.parent
            audit = json.loads((folder / 'independent-account-audit.json').read_text())
            archive, = (folder / 'native_results').glob('*.zip')
            for path,key in ((run / 'registered_plan.json','plan_sha256'),
                             (result,'result_sha256'),(archive,'archive_sha256')):
                if sha(path) != audit[key]:
                    raise ValueError('Frozen account audit mismatch')
            with zipfile.ZipFile(archive) as z:
                filename, = [n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                trades = next(iter(json.loads(z.read(filename))['strategy'].values()))['trades']
            events = []
            for trade in trades:
                pair = trade['pair']
                if pair not in cache:
                    stem = pair.replace('/','_').replace(':','_')
                    frames = []
                    for suffix in ('1h-funding_rate','1h-mark'):
                        relative = f'futures/{stem}-{suffix}.feather'
                        path = market / relative
                        if sha(path) != checksums[relative]:
                            raise ValueError('Frozen market input mismatch: ' + relative)
                        frames.append(pd.read_feather(path))
                    merged = frames[0].merge(frames[1][['date','open']],on='date',validate='one_to_one')
                    cache[pair] = dict(zip(merged.date.astype('datetime64[ms, UTC]').astype('int64'),
                                           zip(merged.funding_rate,merged.open)))
                qty = 0.0
                for order in sorted(trade['orders'],key=lambda x:x['order_filled_timestamp']):
                    ts = order['order_filled_timestamp']
                    qty += order['amount'] * (1 if order['ft_is_entry'] else -1)
                    rate, mark = cache[pair].get(ts,(0,0))
                    if rate:
                        events.append({'pair':pair,'timestamp_utc':pd.to_datetime(ts,unit='ms',utc=True).isoformat(),
                                       'post_fill_qty':qty,'funding_rate':float(rate),'mark_open':float(mark),
                                       'abs_component_usdt':abs(qty*float(rate)*float(mark))})
            accounts.append({'account':folder.name,'event_count':len(events),
                             'abs_component_sum_usdt':math.fsum(e['abs_component_usdt'] for e in events),
                             'events':events,'archive_sha256':sha(archive),'account_audit_sha256':sha(folder/'independent-account-audit.json')})
    if len(accounts) != 6:
        raise ValueError('Expected exactly six frozen accounts')
    out = {'schema':'cz.funding-timing-conditional-sensitivity.v1','generated_utc':datetime.now(timezone.utc).isoformat(),
           'source_sha256':sha(Path(__file__)),'data_receipt_sha256':sha(receipt),'accounts':accounts,
           'scope':'Triangle sum of absolute reconstructed post-fill quantity * nonzero funding rate * mark for all filled orders at funding timestamps. Only a conditional timing sensitivity, not a proven total equity or drawdown error bound, actual settlement or live risk bound.',
           'assumptions':['All other components and trade histories are exact', 'Only these same-time funding components are shifted', 'Native minified order export cannot establish original order placement time'],
           'orders_enabled':False}
    with OUT.open('x') as f:
        json.dump(out,f,indent=2);f.write('\n')
    print(json.dumps([{k:a[k] for k in ('account','event_count','abs_component_sum_usdt')} for a in accounts],indent=2))

if __name__ == '__main__':
    main()
