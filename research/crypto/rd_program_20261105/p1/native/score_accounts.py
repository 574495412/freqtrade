"""Read-only evidence and scorecard generation for registered native accounts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import control_scorecard as metrics

ROOT = HERE.parents[4]
PROGRAM = HERE.parents[1]
DATA = ROOT / 'user_data/crypto_quant/rd_program_20261105_v1'
INPUTS = set()


def read(path):
    INPUTS.add(Path(path))
    return json.loads(Path(path).read_text())


def sha(path):
    return metrics.sha(path)


def frame(path):
    INPUTS.add(Path(path))
    return pd.read_parquet(path).set_index('date')


def trades(output, strategy):
    exports = list((output / 'native_results').glob('*.zip'))
    if len(exports) != 1:
        raise ValueError(f'Expected one native export: {output}')
    INPUTS.add(exports[0])
    with zipfile.ZipFile(exports[0]) as archive:
        names = [name for name in archive.namelist() if name.endswith('.json') and not name.endswith('_config.json')]
        if len(names) != 1:
            raise ValueError('Ambiguous native JSON')
        return json.loads(archive.read(names[0]))['strategy'][strategy]['trades']


def verify_artifacts(output):
    receipt = read(output / 'retained_artifact_hashes.json')
    for rec in receipt['files']:
        path = output / rec['relative_path']
        if not path.resolve().is_relative_to(output.resolve()) or path.is_symlink():
            raise ValueError('Invalid artifact path')
        if path.stat().st_size != rec['bytes'] or sha(path) != rec['sha256']:
            raise ValueError(f'Artifact drift: {path}')
    return len(receipt['files'])


def registration(run_id, suffix=''):
    if run_id == 'P1_B2_AVERAGE_NATIVE_spot_S_base' and not suffix:
        suffix = '__retry1'
    path = HERE / 'registrations' / (run_id + suffix) / 'run_manifest.json'
    manifest = read(path)
    actual = DATA / 'registrations' / (run_id + suffix) / 'run_manifest.json'
    if sha(path) != sha(actual):
        raise ValueError('Public/runtime manifest mismatch')
    start = read(actual.parent / 'start.json')
    if start['manifest_sha256'] != sha(actual):
        raise ValueError('Execution manifest mismatch')
    if manifest['orders_enabled'] or manifest['economic_qualified'] or manifest['RD1_applied']:
        raise ValueError('Native baseline flags changed')
    return manifest, ROOT / manifest['isolated_output_path']


def historical_output(run):
    if run['baseline_id'] != 'B3_NFI_X8_NATIVE':
        return None
    market, window, cost = run['market'], run['window_id'], run['cost_id']
    root = ROOT / 'user_data/crypto_quant'
    if market == 'spot':
        folder = 'spot_baseline_long_run_20261105_v001' if window == 'L' else 'spot_baseline_short_run_20261105_v003'
        return root / folder / ('nfi-x8-spot-cost' + ('13' if cost == 'base' else '20'))
    if window == 'L':
        return root / 'matched_strategy_comparison_20261105_v002' / ('nfi-x8-1x-cost' + ('8' if cost == 'base' else '15'))
    if cost == 'base':
        return root / 'nfi40_post_source_account_20261105_v001/nfi-x8-1x-cost8'
    return None


def score(run):
    manifest, output = registration(run['id'])
    count = verify_artifacts(output)
    result = read(output / 'p1_result.json')
    native = read(output / 'result.json')
    audit = read(output / 'fill_cash_audit.json')
    plan = read(output / 'registered_plan.json')
    config = read(output / 'config.json')
    curve = frame(output / 'five_minute_reference_equity.parquet')
    expected = pd.date_range(manifest['window']['start'], manifest['window']['end_exclusive'], freq='5min', inclusive='left')
    complete = curve.index.equals(expected) and np.isfinite(curve.equity).all()
    if complete != result['observer']['complete_reference_calendar']:
        raise ValueError('Calendar/receipt mismatch')
    if not audit['passed'] or not result['native_fill_cash_audit_passed']:
        raise ValueError('Cash audit failed')
    if complete and abs(float(curve.equity.iloc[-1]) - result['new_endpoint_reference_equity']) > 1e-9:
        raise ValueError('Endpoint mismatch')
    if len(curve) and curve.index[0] == expected[0] and abs(float(curve.equity.iloc[0])-1000) > .01:
        raise ValueError('Initial equity mismatch')
    positive_path = complete and bool((curve.equity > 0).all())
    native_trades = trades(output, native['strategy'])
    row = {
        'id': run['id'], 'baseline': run['baseline_id'], 'market': run['market'],
        'window': run['window_id'], 'cost_id': run['cost_id'],
        'output_directory': str(output.relative_to(ROOT)),
        'durable_artifacts_verified': count, 'cash_audit_passed': audit['passed'],
        'cash_aggregate_error_usdt': audit['aggregate_error_usdt'],
        'initial_equity_usdt': 1000,
        'terminal_reference_equity_usdt': result['new_endpoint_reference_equity'],
        'net_reference_return': result['new_endpoint_reference_return'],
        'valuation': result['observer']['valuation'], 'complete_reference_calendar': bool(complete),
        'missing_reference_rows': result['observer']['missing_rows'],
        'drawdown': metrics.drawdown_stats(curve.equity) if positive_path else None,
        'calendar_metrics': metrics.calendar_stats(curve.equity) if positive_path else None,
        'reference_initial_500_floor_breached': bool((curve.equity <= 500).any()),
        'reference_insolvent': bool((curve.equity <= 0).any()),
        'exposure': {
            'gross_mean_over_nav': float((curve.gross/curve.equity).mean()) if positive_path else None,
            'gross_max_over_nav': float((curve.gross/curve.equity).max()) if positive_path else None,
            'nonzero_gross_snapshot_fraction': float((curve.gross > 0).mean()) if complete else None,
            'net_exposure': 'not reconstructed; short direction not present in aggregate gross observer',
        },
        'campaign_counts_at_old_hourly_anchor': result['old_hourly_anchor_metrics'],
        'native_terminal': {key: native[key] for key in ['native_terminal_return','native_terminal_balance','native_terminal_trade_count','native_terminal_exit_times','native_funding_net_usdt']},
        'native_terminal_not_used_for_ranking': True,
        'native_natural_closed_campaigns': sum(t['exit_reason'] != 'force_exit' for t in native_trades),
        'native_force_exit_campaigns': sum(t['exit_reason'] == 'force_exit' for t in native_trades),
        'model_turnover_and_fees_including_terminal_force_exit': {
            'one_sided_notional_over_initial_capital': sum(o['amount']*o['safe_price'] for t in native_trades for o in t['orders'])/1000,
            'fee_and_execution_proxy_usdt': sum(o['amount']*o['safe_price']*(t['fee_open'] if o['ft_is_entry'] else t['fee_close']) for t in native_trades for o in t['orders']),
            'scope': 'native export only; includes synthetic terminal exits, not matched observer-window cost decomposition',
        },
        'single_pair_full_account_contribution_share': None,
        'contribution_status': 'not reconstructed from aggregate observer; partial exits and final campaign funding cannot certify full per-pair path',
        'capacity_status': 'unknown; historical orderbook/depth/queues unavailable',
        'five_minute_executable_or_futures_mark_verified': False,
        'economic_acceptance': False, 'RD1_applied': False, 'orders_enabled': False,
        'production_eligible': False,
    }
    previous = historical_output(run)
    if previous is None:
        row['historical_parity'] = {'status': 'no_matching_archived_account_assigned'}
    else:
        old = read(previous / 'result.json')
        oldplan = read(previous / 'registered_plan.json')
        oldconfig = read(previous / 'config.json')
        identity = {k: oldplan[k] == plan[k] for k in ['start','end','pairs','initial_equity_usdt']}
        identity['fee'] = oldconfig['fee'] == config['fee']
        identity['strategy'] = old['strategy'] == native['strategy']
        equal_trades = trades(previous, native['strategy']) == native_trades
        delta = result['old_hourly_anchor_metrics'][0]['ending_marked_equity'] - old['periods'][0]['ending_marked_equity']
        row['historical_parity'] = {'status': 'exact_native_trades_and_hourly_anchor_within_tolerance' if all(identity.values()) and equal_trades and abs(delta)<=.01 else 'difference_requires_explanation',
            'previous_output': str(previous.relative_to(ROOT)), 'identity_checks': identity,
            'all_trade_order_funding_fields_literal_equal': equal_trades,
            'hourly_anchor_equity_delta_usdt': delta,
            'native_terminal_balance_delta_usdt': native['native_terminal_balance'] - old['native_terminal_balance'],
            'scope': 'engineering parity only; cannot certify event availability or live settlement'}
    return row


def determinism():
    registered = read(HERE / 'determinism_registration.json')
    results = []
    for case in registered['runs']:
        run_id = case['run_id']
        _, original = registration(run_id)
        _, repeated = registration(run_id, '__determinism1')
        verify_artifacts(repeated)
        strategy = read(original/'result.json')['strategy']
        checks = {
            'all_trade_order_funding_fields_literal_equal': trades(original,strategy) == trades(repeated,strategy),
            'every_five_minute_observation_equal': frame(original/'five_minute_reference_equity.parquet').equals(frame(repeated/'five_minute_reference_equity.parquet')),
            'fill_cash_audit_equal': read(original/'fill_cash_audit.json') == read(repeated/'fill_cash_audit.json'),
        }
        results.append({'id':run_id, 'checks': checks, 'passed': all(checks.values())})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    metrics.self_check()
    registry = read(PROGRAM/'experiment_registry.json')
    rows = [score(run) for run in registry['runs'] if run['baseline_id'] in ('B2_AVERAGE_NATIVE','B3_NFI_X8_NATIVE')]
    controls = read(HERE.parent/'control_scorecard.json')['rows']
    for row in rows:
        matched = [r for r in controls if (r['market'],r['window'],r['cost_id'])==(row['market'],row['window'],row['cost_id'])]
        row['matched_control_return_differences'] = {r['baseline']:row['net_reference_return']-r['net_reference_return'] if row['net_reference_return'] is not None else None for r in matched}
    replay = determinism()
    result = {'schema_version':1,'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':sha(Path(__file__)),'metric_helper_sha256':sha(Path(metrics.__file__)),
        'native_account_count':len(rows),'control_account_count':len(controls), 'rows':rows,
        'determinism':replay,'new_financial_runs_by_this_script':0,
        'input_receipts':[{'path':str(p.relative_to(ROOT)), 'sha256':sha(p)} for p in sorted(INPUTS)],
        'evidence_class':'retrospective_engineering_reference_not_strategy_qualification',
        'orders_enabled':False,'production_eligible':False}
    with args.output.open('x') as stream:
        json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'native_accounts':len(rows),'determinism_passes':sum(r['passed'] for r in replay),
        'historical_parity':{r['id']:r['historical_parity']['status'] for r in rows if r['baseline']=='B3_NFI_X8_NATIVE'}}))


if __name__ == '__main__':
    main()
