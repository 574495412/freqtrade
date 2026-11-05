"""Post-hoc mark valuation of frozen P1 futures positions; no strategy execution.

Cash, modeled funding, orders and their pre-event observation clock stay fixed.
Only unrealized P&L and the hypothetical exit-cost reserve change with price.
This is a named valuation sensitivity, not a new executable account history.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PROGRAM = HERE.parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(PROGRAM / 'p1/native'))
import score_accounts as native

metrics = native.metrics
DATA = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1'
ASSEMBLY = DATA / 'mark5m_recovery_v001/assembly_result.json'
REGISTRATION = HERE / 'mark_valuation_registration.json'
OUTPUT = DATA / 'mark_valuation_v001'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def inventory(trade, index):
    """Position just BEFORE each snapshot; final artificial close is excluded."""
    if not len(index) or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError('Invalid observation index')
    timestamps = index.as_unit('ns').asi8
    changes = np.zeros(len(index))
    orders = trade['orders']
    forced = trade['exit_reason'] == 'force_exit'
    if forced and (not orders or orders[-1]['ft_is_entry']):
        raise ValueError('Invalid terminal force exit')
    previous = None
    balance = 0.
    for i, order in enumerate(orders):
        if forced and i == len(orders) - 1:
            continue
        at = pd.Timestamp(order['order_filled_timestamp'], unit='ms', tz='UTC')
        if at < index[0] or (previous is not None and at < previous):
            raise ValueError('Unexpected fill chronology')
        previous = at
        amount = float(order['amount'])
        if not np.isfinite(amount) or amount <= 0:
            raise ValueError('Invalid fill amount')
        delta = amount if order['ft_is_entry'] else -amount
        balance += delta
        if balance < -1e-8:
            raise ValueError('Exit exceeds entry inventory')
        insertion = int(np.searchsorted(timestamps, at.value, side='right'))
        if insertion < len(index):
            changes[insertion] += delta
    quantity = np.cumsum(changes)
    quantity[np.abs(quantity) < 1e-8] = 0
    if (quantity < 0).any():
        raise ValueError('Negative position')
    return quantity


def valuation_delta(quantity, reference, mark, is_short, fee_close):
    """Direction affects P&L, but the exit reserve costs money on both sides."""
    arrays = [np.asarray(x, dtype=float) for x in (quantity, reference, mark)]
    if len({x.shape for x in arrays}) != 1:
        raise ValueError('Unaligned valuation inputs')
    quantity, reference, mark = arrays
    if not all(np.isfinite(x).all() for x in arrays) or (quantity < 0).any() or (reference <= 0).any() or (mark <= 0).any():
        raise ValueError('Invalid valuation inputs')
    if not np.isfinite(fee_close) or not 0 <= fee_close < 1:
        raise ValueError('Invalid close cost')
    difference = quantity * (mark - reference)
    return (-1 if is_short else 1) * difference - difference * fee_close


def mark_prices(assembly, window, pair, index, cache):
    key = (window, pair)
    if key not in cache:
        matches = [x for x in assembly['derived_files'] if (x['window'], x['pair']) == key]
        if len(matches) != 1:
            raise ValueError('Missing/duplicate mark source')
        rec = matches[0]
        path = ROOT / rec['path']
        if metrics.sha(path) != rec['sha256']:
            raise ValueError('Mark source drift')
        frame = pd.read_feather(path).set_index('date')
        if not frame.index.equals(index) or not rec['grid_complete']:
            raise ValueError('Mark observation grid differs')
        cache[key] = frame.open.to_numpy(dtype=float)
    return cache[key]


def collect_inputs():
    """Freeze upstream receipts before looking at new valuation statistics."""
    expected = {}
    def add(path, digest=None):
        path = Path(path)
        absolute = path if path.is_absolute() else ROOT / path
        relative = str(absolute.relative_to(ROOT))
        value = digest or metrics.sha(absolute)
        if relative in expected and expected[relative] != value:
            raise ValueError('Conflicting upstream receipt')
        expected[relative] = value
    for path in [Path(__file__), Path(native.__file__), Path(metrics.__file__), Path(native.audit_positions.__file__), HERE/'test_mark_valuation.py', ASSEMBLY, ASSEMBLY.parent/'assembly_execution.json', PROGRAM/'experiment_registry.json', PROGRAM/'p1/native/native_scorecard.json', PROGRAM/'p1/control_scorecard.json', PROGRAM/'p1/control_results.json']:
        add(path)
    for rec in read(ASSEMBLY)['input_receipts'] + read(ASSEMBLY)['derived_files'] + read(PROGRAM/'p1/native/native_scorecard.json')['input_receipts']:
        add(rec['path'], rec['sha256'])
    controls = read(PROGRAM/'p1/control_results.json')['rows']
    for row in controls:
        manifest_path = PROGRAM/'p1/manifests'/f"{row['id']}.json"
        add(manifest_path, row['manifest_sha256'])
        manifest = read(manifest_path)
        out = ROOT/manifest['isolated_output_path']
        add(out/'result.json', row['result_sha256'])
        add(out/'reference_equity.parquet', row['output_sha256'])
        for rec in manifest['data_receipt_hashes'].values():
            add(rec['path'], rec['sha256'])
    return expected


def verify_inputs(receipts):
    for relative, digest in receipts.items():
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT) or path.is_symlink() or metrics.sha(path) != digest:
            raise ValueError(f'Input changed: {relative}')


def register():
    if REGISTRATION.exists() or OUTPUT.exists():
        raise ValueError('Registration/output already exists')
    receipts = collect_inputs()
    verify_inputs(receipts)
    runs = [r['id'] for r in read(PROGRAM/'experiment_registry.json')['runs'] if r['market']=='futures']
    if len(runs) != 16 or len(set(runs)) != 16:
        raise ValueError('Expected exactly 16 existing futures cells')
    write_new(REGISTRATION, {
        'schema_version':1, 'registered_at_utc':utc_now(), 'run_ids':runs,
        'output_directory':str(OUTPUT.relative_to(ROOT)), 'input_receipts':receipts,
        'method':'Frozen pre-event inventory and original cash/funding; NAV += sum(direction*q*(mark_OPEN-trade_OPEN) - q*(mark_OPEN-trade_OPEN)*fee_close).',
        'terminal_force_exit':'Exclude final artificial native order, even if backdated by main timeframe.',
        'costs':'Same frozen per-side commission plus execution proxy; no extra fee layer; exit reserve changes with valuation.',
        'price':'Official 5m mark OPEN; never bar high/low/close or future values; row provenance retained in source.',
        'validation':'All upstream hashes, all observation grids, original gross within0.01 USDT; post-run input hashes unchanged.',
        'financial_runs':0, 'strategy_parameters_changed':False, 'orders_enabled':False,
        'economic_qualified':False, 'historical_receipt_times_certified':False,
        'funding_fill_ordering_certified':False, 'executable_quotes_certified':False,
    })
    print(json.dumps({'registered_existing_accounts':len(runs),'input_receipts':len(receipts)}))


def score(run, assembly, old, cache, controls):
    run_id = run['id']
    delta = gross = marked_gross = None
    if run['baseline_id'] in ('B2_AVERAGE_NATIVE','B3_NFI_X8_NATIVE'):
        manifest, out = native.registration(run_id)
        native.verify_artifacts(out)
        source = native.frame(out/'five_minute_reference_equity.parquet')
        ref, expected_gross = source.equity, source.gross
        trades = native.trades(out, read(out/'result.json')['strategy'])
        prices = native.load_prices(read(out/'registered_plan.json'), 'futures', trades)
        delta, gross, marked_gross = (np.zeros(len(ref)) for _ in range(3))
        for trade in trades:
            qty = inventory(trade, ref.index)
            price = prices[trade['pair']].reindex(ref.index).to_numpy(dtype=float)
            mark = mark_prices(assembly, run['window_id'], trade['pair'], ref.index, cache)
            delta += valuation_delta(qty, price, mark, trade['is_short'], trade['fee_close'])
            gross += qty*price
            marked_gross += qty*mark
    else:
        manifest, out, result, source = metrics.check_receipts(PROGRAM/'p1/manifests'/f'{run_id}.json', controls[run_id])
        ref, expected_gross = source.reference_equity, source.gross_notional
        delta, gross, marked_gross = (np.zeros(len(ref)) for _ in range(3))
        if run['baseline_id']=='B1_BTC_HOLD':
            qty = np.where(ref.index>pd.Timestamp(result['entry_time']), float(result['quantity']), 0.)
            price = pd.read_feather(ROOT/manifest['data_receipt_hashes']['prices']['path']).set_index('date').open.reindex(ref.index).to_numpy(dtype=float)
            mark = mark_prices(assembly, run['window_id'], 'BTC/USDT:USDT', ref.index, cache)
            delta = valuation_delta(qty, price, mark, False, manifest['cost_scenario']['total_bps_per_side']/10000)
            gross, marked_gross = qty*price, qty*mark
    expected_index = pd.date_range(manifest['window']['start'], manifest['window']['end_exclusive'], freq='5min', inclusive='left')
    if not ref.index.equals(expected_index) or not np.isfinite(ref).all():
        raise ValueError('Incomplete original observation grid')
    gross_error = float(np.max(np.abs(gross-expected_gross.to_numpy(dtype=float))))
    if gross_error > .01:
        raise ValueError(f'Original inventory mismatch: {run_id}, {gross_error}')
    marked = pd.Series(ref.to_numpy()+delta, index=ref.index)
    if not np.isfinite(marked).all() or marked.iloc[0] != 1000:
        raise ValueError('Invalid marked NAV')
    old_row = old[run_id]
    if abs(float(ref.iloc[-1])-old_row['terminal_reference_equity_usdt']) > 1e-9:
        raise ValueError('Original endpoint changed')
    reference_mdd = metrics.drawdown_stats(ref)['mdd']
    if abs(reference_mdd-old_row['drawdown']['mdd']) > 1e-12:
        raise ValueError('Original drawdown changed')
    path = OUTPUT/f'{run_id}.parquet'
    pd.DataFrame({'date':ref.index,'original_reference_equity':ref.to_numpy(),'official_mark_equity_conditional_cash':marked.to_numpy(),'valuation_delta':delta,'original_gross':gross,'mark_gross':marked_gross}).to_parquet(path,index=False)
    positive = bool((marked>0).all())
    row = {'id':run_id,'baseline':run['baseline_id'],'window':run['window_id'],'cost_id':run['cost_id'],
        'rows':len(ref),'complete_observation_grid':True,'original_gross_max_error_usdt':gross_error,
        'original_reference_net_return':float(ref.iloc[-1]/1000-1),
        'official_mark_net_return_conditional_cash':float(marked.iloc[-1]/1000-1),
        'terminal_official_mark_equity_conditional_cash':float(marked.iloc[-1]),
        'terminal_valuation_delta_usdt':float(delta[-1]),'max_abs_valuation_delta_usdt':float(np.abs(delta).max()),
        'original_reference_mdd':reference_mdd,'mark_drawdown':metrics.drawdown_stats(marked) if positive else None,
        'mark_calendar_metrics':metrics.calendar_stats(marked) if positive else None,
        'original_500_floor_breached':bool((ref<=500).any()),'mark_500_floor_breached':bool((marked<=500).any()),
        'mark_insolvent':not positive,'mark_peak_gross_over_nav':float(np.max(marked_gross/marked)) if positive else None,
        'output':{'path':str(path.relative_to(ROOT)),'sha256':metrics.sha(path)},
        'RD1_applied':False,'economic_qualified':False,'orders_enabled':False}
    print(json.dumps({k:row[k] for k in ['id','official_mark_net_return_conditional_cash','terminal_valuation_delta_usdt','original_gross_max_error_usdt']}),flush=True)
    return row


def run():
    reg = read(REGISTRATION)
    verify_inputs(reg['input_receipts'])
    OUTPUT.mkdir(exist_ok=False)
    write_new(OUTPUT/'start.json',{'started_at_utc':utc_now(),'registration_sha256':metrics.sha(REGISTRATION),'all_input_receipts_verified':True,'financial_runs':0})
    assembly = read(ASSEMBLY)
    if assembly['grid_complete_files'] != 80 or not assembly['all_frozen_hourly_opens_equal_within_1e_8']:
        raise ValueError('Assembly incomplete or hourly inputs differ')
    metrics.self_check()
    old = {r['id']:r for p in [PROGRAM/'p1/native/native_scorecard.json',PROGRAM/'p1/control_scorecard.json'] for r in read(p)['rows']}
    controls = {r['id']:r for r in read(PROGRAM/'p1/control_results.json')['rows']}
    runs = [r for r in read(PROGRAM/'experiment_registry.json')['runs'] if r['market']=='futures']
    if [r['id'] for r in runs] != reg['run_ids']:
        raise ValueError('Registered cells changed')
    cache = {}
    rows = [score(r,assembly,old,cache,controls) for r in runs]
    verify_inputs(reg['input_receipts'])
    write_new(OUTPUT/'result.json',{
        'schema_version':1,'finished_at_utc':utc_now(),'state':'completed_conditional_mark_revaluation',
        'registration_sha256':metrics.sha(REGISTRATION),'rows':rows,'existing_accounts_revalued':len(rows),
        'inputs_reverified_after':True,'financial_runs':0,'orders_enabled':False,'economic_qualified':False,
        'original_P1_artifacts_modified':False,'historical_received_at_certified':False,'funding_fill_ordering_certified':False,
        'executable_bid_ask_certified':False,
        'scope':'Only original frozen positions revalued; cash/funding chronology remains the original conditional model. No strategy or fill replay, no risk-trigger recomputation, no executable return or qualification claim.',
    })


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['register','run'])
    args=parser.parse_args()
    register() if args.action=='register' else run()
