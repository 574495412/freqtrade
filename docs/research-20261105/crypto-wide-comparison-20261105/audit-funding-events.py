"""Audit archived funding by event and fill; do not execute strategies/orders."""
import hashlib
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

CZ = Path('/Users/chrise/GitHub/CZ-Agent')
FT = Path('/Users/chrise/GitHub/freqtrade')
DATA = FT / 'user_data/crypto_quant'
MANIFEST = CZ / 'vendor/FREQTRADE_SOURCE.json'
ENGINE = CZ / 'vendor/freqtrade'
OUT = Path(__file__).with_name('funding-event-boundary-audit.json')
FOLDERS = [DATA / 'wide_strategy_comparison_20261105_v001',
           DATA / 'wide_strategy_cost15_20261105_v001',
           DATA / 'wide_strategy_x7_20261105_v001']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if OUT.exists():
        raise ValueError('Refuse to overwrite audit')
    manifest = json.loads(MANIFEST.read_text())
    srcs = ['freqtrade/optimize/backtesting.py', 'freqtrade/exchange/exchange.py',
            'freqtrade/persistence/trade_model.py']
    for name in srcs:
        if sha(ENGINE / name) != manifest['files'][name]:
            raise ValueError('Pinned engine source changed: ' + name)
    plans = [json.loads((p / 'registered_plan.json').read_text()) for p in FOLDERS]
    if any((p['datadir'], p['data_receipts']) != (plans[0]['datadir'], plans[0]['data_receipts']) for p in plans):
        raise ValueError('Funding sources differ')
    receipt = Path(plans[0]['data_receipts']['path'])
    if sha(receipt) != plans[0]['data_receipts']['sha256']:
        raise ValueError('Data manifest changed')
    data_sha = json.loads(receipt.read_text())
    directory = Path(plans[0]['datadir'])
    cache, data_records, outputs = {}, {}, []
    for folder in FOLDERS:
        for archive in sorted(folder.glob('*/native_results/*.zip')):
            account = archive.parent.parent
            prior = json.loads((account / 'independent-account-audit.json').read_text())
            if sha(archive) != prior['archive_sha256'] or sha(account / 'result.json') != prior['result_sha256']:
                raise ValueError('Account archive/result changed')
            with zipfile.ZipFile(archive) as z:
                name, = [n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                trades = next(iter(json.loads(z.read(name))['strategy'].values()))['trades']
            events, discrepancies = [], []
            for trade in trades:
                pair = trade['pair']
                if pair not in cache:
                    stem = pair.replace('/', '_').replace(':', '_')
                    rels = [f'futures/{stem}-1h-funding_rate.feather', f'futures/{stem}-1h-mark.feather']
                    for rel in rels:
                        if sha(directory / rel) != data_sha[rel]:
                            raise ValueError('Frozen market data changed: ' + rel)
                        data_records[rel] = data_sha[rel]
                    funding, mark = (pd.read_feather(directory / rel) for rel in rels)
                    cache[pair] = funding.merge(mark[['date', 'open']], on='date', how='inner', validate='one_to_one')
                frame = cache[pair]
                start = pd.to_datetime(trade['open_timestamp'], unit='ms', utc=True)
                end = pd.to_datetime(trade['close_timestamp'], unit='ms', utc=True)
                frame = frame[(frame.date >= start) & (frame.date <= end)]
                orders = sorted(trade['orders'], key=lambda o: o['order_filled_timestamp'])
                ticks = np.array([o['order_filled_timestamp'] for o in orders], dtype='int64')
                deltas = np.array([o['amount'] * (1 if o['ft_is_entry'] else -1) for o in orders])
                qty = np.r_[0, np.cumsum(deltas)]
                ms = frame.date.astype('datetime64[ms, UTC]').astype('int64').to_numpy()
                left = np.searchsorted(ticks, ms, 'left')
                right = np.searchsorted(ticks, ms, 'right')
                rates = frame.funding_rate.to_numpy()
                marks = frame.open.to_numpy()
                sign = 1 if trade['is_short'] else -1
                baseline = sign * float(np.sum(rates * marks * qty[left]))
                model = baseline
                for i in np.nonzero(left != right)[0]:
                    extra_qty = math.fsum(float(qty[j + 1]) for j in range(left[i], right[i]))
                    extra = sign * float(rates[i] * marks[i] * extra_qty)
                    model += extra
                    events.append({'pair':pair, 'trade_open':trade['open_date'], 'trade_close':trade['close_date'],
                                   'side':'short' if trade['is_short'] else 'long', 'funding_time':frame.date.iloc[i].isoformat(),
                                   'funding_rate':float(rates[i]), 'mark_open':float(marks[i]),
                                   'qty_before':float(qty[left[i]]),
                                   'fills_at_boundary':[{'entry':bool(orders[j]['ft_is_entry']), 'quantity':orders[j]['amount'],
                                                         'quantity_after':float(qty[j + 1])} for j in range(left[i],right[i])],
                                   'baseline_strict_pre_fill_funding_usdt':sign*float(rates[i]*marks[i]*qty[left[i]]),
                                   'additional_model_funding_usdt':extra})
                error = model - trade['funding_fees']
                discrepancies.append({'pair':pair, 'trade_open':trade['open_date'],
                                      'baseline_funding_usdt':baseline, 'model_funding_usdt':model,
                                      'native_funding_usdt':trade['funding_fees'], 'model_minus_native_usdt':error})
            max_error = max((abs(x['model_minus_native_usdt']) for x in discrepancies), default=0)
            if max_error > 1e-7:
                raise ValueError('Native funding mismatch: ' + str(account))
            extra = math.fsum(e['additional_model_funding_usdt'] for e in events)
            outputs.append({'account':account.name, 'archive_sha256':sha(archive), 'trades':len(trades),
                            'funding_boundary_events_with_fills':len(events),
                            'events_with_multiple_same_time_fills':sum(len(e['fills_at_boundary']) > 1 for e in events),
                            'events_with_nonzero_additional_model_funding':sum(abs(e['additional_model_funding_usdt']) > 1e-12 for e in events),
                            'net_additional_model_funding_usdt':extra,
                            'net_additional_model_funding_percent_of_initial_1000':extra / 1000 * 100,
                            'maximum_model_minus_native_abs_usdt':max_error, 'events':events})
    if len(outputs) != 6:
        raise ValueError('Expected six independent account archives')
    record = {'schema':'cz.funding-boundary-audit.v1','created_at_utc':datetime.now(timezone.utc).isoformat(),
              'engine_commit':manifest['commit'], 'engine_manifest_sha256':sha(MANIFEST),
              'engine_sources':{name:manifest['files'][name] for name in srcs},
              'input_data_receipt':{'path':str(receipt),'sha256':sha(receipt)}, 'funding_and_mark_file_hashes':data_records,
              'baseline':'Fee per event on position strictly before same-time fills. This is an explicit accounting reference, not verified actual exchange settlement.',
              'model':'Baseline plus funding on the position immediately after each same-time fill; independently matched to all native trade funding totals.',
              'scope':'Archived simulated fills and native funding. No strategy replay, no market execution or account settlement evidence. Additional amount is a fixed-fill accounting difference, not a counterfactual backtest or a claim that exchange charged twice.',
              'result_affects_existing_archives':False, 'orders_enabled':False, 'accounts':outputs}
    OUT.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in a.items() if k != 'events'} for a in outputs],indent=2))

if __name__ == '__main__':
    main()
