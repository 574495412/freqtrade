"""Isolate funding-clock attribution on sealed fills, without running strategies."""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE.parent/'historical_funding_universe_20261106'
NATIVE = HERE.parent/'factor_cost_futures_20261106'
sys.path[:0] = [str(SOURCE),str(NATIVE)]
from capture import LONG, SHORT, bounds, now, sha, verify, write
from reconstruct_account import funding_projection, reconstruct

# Import the completed funding audit under an unambiguous name.
from importlib.util import module_from_spec, spec_from_file_location
spec = spec_from_file_location('frozen_public_funding_audit',SOURCE/'audit.py')
public = module_from_spec(spec)
spec.loader.exec_module(public)

PLAN = HERE/'registration.json'
RESULT = HERE/'result.json'
WINDOWS = {'L':LONG,'S':SHORT}
OUT = ROOT/'user_data/crypto_quant/rd_program_20261105_v1/funding_order_sensitivity_v001'
CASH = 1000.
HOUR = 3_600_000
NOISE = Decimal('1e-9')


def read(path):
    return json.loads(path.read_text())


def artifacts(case):
    directory = public.ACCOUNT_ROOT/case['id']
    manifest = directory/'artifact_hashes.json'
    hashes = read(manifest)
    archive, = (directory/'native_results').glob('*.zip')
    paths = [directory/'result.json',directory/'model_equity.parquet',archive]
    for path in paths:
        public.checked(path,hashes[str(path.relative_to(directory))])
    return manifest,paths


def register():
    inputs = [Path(__file__),NATIVE/'reconstruct_account.py',SOURCE/'audit.py',
              SOURCE/'verification.json',SOURCE/'audit_result.json',public.LONG_SOURCE,public.SHORT_SOURCE]
    public_proof = read(SOURCE/'verification.json')
    verify(public_proof)
    cases = []
    for window, path in WINDOWS.items():
        source = read(path)
        verify(source)
        result_path = path.with_name('results.json')
        results = {r['run_id']:r for r in read(result_path)['rows']}
        inputs.extend((path,result_path,ROOT/source['runs'][0]['data_receipt']))
        for run in source['runs']:
            manifest,paths = artifacts(run)
            expected = results[run['id']]
            if expected['artifact_manifest_sha256']!=sha(manifest.read_bytes()):
                raise ValueError('Sealed account manifest no longer matches its audit')
            inputs.extend([manifest,*paths])
            cases.append(dict(window=window,run=run,prior_terminal_funding=expected['audit']['terminal_projected_funding']))
    write(PLAN,dict(registered_at_utc=now(),cases=cases,
                    input_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in inputs},
                    policy=dict(
                        original='Reconstruct all 5m NAV with the native inclusive funding projection.',
                        shifted='Change only funding event index from native hour to original public millisecond.',
                        one_book='One public settlement per symbol and clock, hourly funding mark held fixed, '
                                 'use frozen quantities before or after fills exactly at the public millisecond.',
                        bound='For the one-book envelope only, any saved fill between nominal hour and exact '
                              'public time can fall on either side of the assumed position cutoff.',
                        observation='One-book cash at the first observed 5m clock strictly after the public event. '
                                    'This is a diagnostic sample convention, not historical receipt.',
                        retained='Fixed fills, fees, risk flags and marks. No strategy, orders or account creation.'),
                    initial_equity_usdt=CASH,leverage=1,financial_accounts=0))


def inventory(fills):
    running = Decimal(0)
    for at,qty in sorted(fills.items()):
        delta = sum(qty,Decimal(0))
        running += delta
        if abs(running)<NOISE:
            running = Decimal(0)
        if running<0:
            raise ValueError('Negative long-only inventory')
        yield at,running,qty


def held(prefix,at):
    i = np.searchsorted(prefix[0],at,side='left')
    return prefix[1][i-1] if i else Decimal(0)


def one_book(fills,exact_index,hour_index,unit,clock):
    """Cashflows for two exact ties and a conservative nominal-to-public bound."""
    steps = list(inventory(fills))
    timestamps = np.array([row[0] for row in steps],dtype='int64')
    prefix = (timestamps,[row[1] for row in steps])
    changes = np.zeros((4,len(clock)))
    ambiguous, exact_ties = 0,0
    for exact,nominal,value in zip(exact_index,hour_index,unit,strict=True):
        qty_before = held(prefix,exact)
        qty_after = qty_before+sum(fills.get(exact,()),Decimal(0))
        if abs(qty_after)<NOISE:
            qty_after = Decimal(0)
        base = held(prefix,nominal)
        affected = np.searchsorted(timestamps,[nominal,exact],side='left')
        left,right = int(affected[0]),int(affected[1])
        if right<len(timestamps) and timestamps[right]==exact:
            right += 1
        selected = [q for _,_,deltas in steps[left:right] for q in deltas]
        minimum = max(Decimal(0),base+sum((q for q in selected if q<0),Decimal(0)))
        maximum = base+sum((q for q in selected if q>0),Decimal(0))
        if not minimum<=min(qty_before,qty_after)<=max(qty_before,qty_after)<=maximum:
            raise ValueError('Exact quantities fall outside the model cutoff envelope')
        p,q = -float(qty_before)*value,-float(qty_after)*value
        a,b = -float(minimum)*value,-float(maximum)*value
        ambiguous += int(a!=b)
        exact_ties += int(p!=q)
        index = np.searchsorted(clock,exact,side='right')
        if index<len(clock):
            changes[:,index] += [p,q,min(a,b),max(a,b)]
    return changes,ambiguous,exact_ties


def sources(case, window, pairs, plan):
    archive_sources = public.funding_sources()[window]
    begin,end = bounds(WINDOWS[window])
    receipts = read(ROOT/case['data_receipt'])
    marks, nominal, exact, keys = {},{},{},{}
    for pair in sorted(pairs):
        mark_path = ROOT/case['mark_paths'][pair]
        public.checked(mark_path,plan['input_hashes'][str(mark_path.relative_to(ROOT))])
        marks[pair] = pd.read_feather(mark_path).set_index('date').open
        stem = pair.replace('/','_').replace(':','_')
        base = ROOT/case['datadir']
        ratefile = f'futures/{stem}-1h-funding_rate.feather'
        markfile = f'futures/{stem}-1h-mark.feather'
        for path in (ratefile,markfile):
            public.checked(base/path,receipts[path])
        rates = pd.read_feather(base/ratefile).set_index('date').funding_rate
        rates = rates.loc[(rates.index>=pd.Timestamp(case['start']))&(rates.index<pd.Timestamp(case['end']))]
        prices = pd.read_feather(base/markfile).set_index('date').open.reindex(rates.index)
        if not np.isfinite(prices).all() or not (prices>0).all():
            raise ValueError('Incomplete frozen hourly mark')
        nominal[pair] = rates*prices
        symbol = pair.split(':')[0].replace('/','')
        official = public.hour_map(public.event_map(symbol,archive_sources[symbol],begin,end))
        hour_index = rates.index.as_unit('ms').asi8
        if set(hour_index)!=official.keys():
            raise ValueError('Frozen funding hours differ from official original')
        exact_index = np.array([official[h][0] for h in hour_index],dtype='int64')
        if any(official[h][1]!=Decimal(str(r)) for h,r in zip(hour_index,rates,strict=True)):
            raise ValueError('Frozen rates differ from original')
        exact[pair] = pd.Series(nominal[pair].to_numpy(),index=pd.to_datetime(exact_index,unit='ms',utc=True))
        keys[pair] = (exact_index,hour_index)
    return marks,nominal,exact,keys


def metrics(equity):
    if not (np.isfinite(equity).all() and np.all(equity>0)):
        raise ValueError('Invalid model equity')
    return dict(terminal_equity_usdt=float(equity[-1]),net_return=float(equity[-1]/CASH-1),
                maximum_5m_drawdown=float(np.max(1-equity/np.maximum.accumulate(equity))))


def account(item):
    case,window = item['run'],item['window']
    _, paths = artifacts(case)
    original,curve_path,archive_path = paths
    recorded = read(original)
    with ZipFile(archive_path) as archive:
        entry, = [n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
        trades = json.loads(archive.read(entry))['strategy'][case['strategy']]['trades']
    curve = pd.read_parquet(curve_path).set_index('date')
    expected = pd.date_range(case['start'],case['end'],freq='5min',inclusive='left')
    if not curve.index.equals(expected):
        raise ValueError('Frozen 5m sample calendar changed')
    marks,nominal,exact,keys = sources(case,window,{trade['pair'] for trade in trades},read(WINDOWS[window]))
    baseline = reconstruct(trades,curve.index,marks,nominal)
    nav = curve.equity.to_numpy()
    error = float(np.max(np.abs(nav-baseline['equity'])))
    if (error>1e-4 or abs(baseline['funding'][-1]-item['prior_terminal_funding'])>1e-6
            or abs(nav[-1]-recorded['ending_model_equity'])>1e-6
            or abs(metrics(nav)['maximum_5m_drawdown']-recorded['max_5m_model_drawdown'])>1e-6):
        raise ValueError('Original funding or full NAV no longer reconstructs')
    shifted = np.zeros(len(curve))
    pair_fills = defaultdict(lambda:defaultdict(list))
    for trade in trades:
        pair = trade['pair']
        orders = trade['orders'][:-1] if trade['exit_reason']=='force_exit' else trade['orders']
        shifted += funding_projection(orders,curve.index,exact[pair])
        for order in orders:
            amount = Decimal(str(order['amount']))
            if amount<=0:
                raise ValueError('Invalid sealed fill quantity')
            pair_fills[pair][order['order_filled_timestamp']].append(amount if order['ft_is_entry'] else -amount)
    clock = curve.index.as_unit('ms').asi8
    components = np.zeros((4,len(clock)))
    ambiguities,exact_ties = 0,0
    for pair,events in pair_fills.items():
        shifts,a,t = one_book(events,*keys[pair],nominal[pair].to_numpy(),clock)
        components += shifts
        ambiguities += a
        exact_ties += t
    before,after,lo_fund,hi_fund = np.cumsum(components,axis=1)
    basis = nav-baseline['funding']
    variants = {
        'frozen_native':nav,'public_ms_native_inclusive':basis+shifted,
        'public_ms_once_before_exact_ties':basis+before,'public_ms_once_after_exact_ties':basis+after,
        'one_book_lower':basis+lo_fund,'one_book_upper':basis+hi_fund,
    }
    lo,hi = variants['one_book_lower'],variants['one_book_upper']
    if not (np.all(lo<=np.minimum(variants['public_ms_once_before_exact_ties'],variants['public_ms_once_after_exact_ties'])+1e-9)
            and np.all(hi+1e-9>=np.maximum(variants['public_ms_once_before_exact_ties'],variants['public_ms_once_after_exact_ties']))):
        raise ValueError('An exact scenario is outside its one-book envelope')
    mdd_bounds = [max(0.,float(np.max(1-hi/np.maximum.accumulate(lo)))),
                  float(np.max(1-lo/np.maximum.accumulate(hi)))]
    output = OUT/(case['id']+'.parquet')
    if output.exists():
        raise FileExistsError('Sensitivity output already exists')
    pd.DataFrame(variants,index=curve.index).to_parquet(output)
    stats = {key:dict(metrics(values),terminal_change_vs_frozen_usdt=float(values[-1]-nav[-1]),
                      max_absolute_path_change_usdt=float(np.max(np.abs(values-nav))))
             for key,values in variants.items()}
    return dict(id=case['id'],strategy=case['strategy'],cost=case['cost'],window=window,
                five_minute_samples=len(curve),baseline_max_nav_error_usdt=error,
                ambiguous_model_position_events=ambiguities,exact_fill_tie_events=exact_ties,
                mdd_bounds=mdd_bounds,variants=stats,
                output=str(output.relative_to(ROOT)),output_sha256=sha(output.read_bytes()))


def run():
    plan = read(PLAN)
    verify(plan)
    OUT.mkdir(parents=True,exist_ok=False)
    rows = []
    for item in plan['cases']:
        row = account(item)
        rows.append(row)
        print(row['id'],row['variants']['one_book_upper']['net_return'],flush=True)
    verify(plan)
    verify(read(LONG))
    verify(read(SHORT))
    write(RESULT,dict(registration_sha256=sha(PLAN.read_bytes()),rows=rows,
                      historical_account_funding_verified=False,
                      full_RD1_financial_admission=False,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
