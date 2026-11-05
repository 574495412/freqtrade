#!/usr/bin/env python3
"""Six registered official recursive commands, with signal observations only.

No execution without --execute. Each native indicator preparation is counted.
The four frozen CLI startups remain unchanged; native X8 startup=800 is added by
unmodified official code and reported separately. No trade backtest is allowed.
Signal columns do not cover custom_exit, DCA callbacks, fills or profitability.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
import time

from runtime import ROOT, environment_guard, exclusive_worker, install_offline_exchange, resource_watch

sys.dont_write_bytecode = True
MANIFEST = Path(__file__).with_name('recursive_manifest.json')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def verify(manifest, key):
    if sha(Path(__file__)) != manifest['runner_sha256'] or sha(Path(__file__).with_name('runtime.py')) != manifest['runtime_sha256']:
        raise ValueError('Registered diagnostic runner changed')
    guard, result = environment_guard(manifest)
    spec = manifest['cases'][key]
    for record in (manifest['strategy'], spec['config'], spec['market_snapshot'], spec['data_receipt']):
        guard.checked(record)
    receipt = json.loads(guard.checked(spec['data_receipt']).read_text())
    for name, digest in spec['selected_data'].items():
        if receipt.get(name) != digest or sha(ROOT / spec['datadir'] / name) != digest:
            raise ValueError('Selected frozen data changed: ' + name)
    config = json.loads(guard.checked(spec['config']).read_text())
    if config.get('add_config_files') or config.get('freqai', {}).get('enabled'):
        raise ValueError('Unregistered additional config or ML mode')
    for field in ('key', 'secret', 'password', 'uid', 'walletAddress', 'privateKey'):
        if config.get('exchange', {}).get(field):
            raise ValueError('Research config contains credentials')
    return guard, result, config


def execute(manifest, key, guard, env_result, config):
    spec = manifest['cases'][key]
    output = ROOT / spec['output_directory']
    output.mkdir(parents=True, exist_ok=False)
    (output / 'utility-userdir').mkdir()
    sanitized = copy.deepcopy(config)
    sanitized.update(dry_run=True, initial_state='stopped', export='none', backtest_cache='none')
    for section in ('telegram', 'webhook', 'api_server'):
        sanitized.setdefault(section, {})['enabled'] = False
    config_path = output / 'diagnostic-config.json'
    save(config_path, sanitized)
    restore_exchange = install_offline_exchange(guard, spec['market_snapshot'])
    from freqtrade.main import main as freqtrade_main
    from freqtrade.optimize.analysis.recursive import RecursiveAnalysis
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.resolvers import StrategyResolver
    import pandas as pd

    original_prepare = RecursiveAnalysis.prepare_data
    original_load = StrategyResolver.load_strategy
    original_backtest = Backtesting.backtest
    original_analyze = RecursiveAnalysis.analyze_indicators
    records = []
    active_strategy = None
    native_indicator_report = {}
    started = time.monotonic()
    signal_fields = ['enter_long', 'enter_short', 'exit_long', 'exit_short']
    checkpoints = pd.to_datetime(manifest['checkpoints'], utc=True)

    def observed_load(local_config):
        nonlocal active_strategy
        active_strategy = original_load(local_config)
        if active_strategy.target_profit_cache.data:
            raise ValueError('Recursive phase cache is not empty')
        return active_strategy

    def no_trade_backtest(*args, **kwargs):
        raise RuntimeError('Recursive diagnostic attempted an unregistered financial backtest')

    def observed_prepare(analysis, holder, pairs):
        number = len(records)
        if number >= manifest['maximum_indicator_preparations_per_command']:
            raise RuntimeError('Registered preparation budget exceeded')
        kind = 'baseline' if number == 0 else 'native_early_lookahead' if number == 1 else 'recursive'
        phase_dir = output / 'phase-userdirs' / f'{number:02d}-{kind}'
        phase_dir.mkdir(parents=True, exist_ok=False)
        record = {'number': number, 'kind': kind, 'from_dt': holder.from_dt.isoformat(),
                  'to_dt': holder.to_dt.isoformat(), 'requested_startup': getattr(holder, 'startup_candle', None),
                  'state': 'STARTED', 'financial_backtests': 0}
        records.append(record)
        save(output / 'phases.json', records)
        previous_userdir = analysis.local_config['user_data_dir']
        analysis.local_config['user_data_dir'] = phase_dir
        try:
            original_prepare(analysis, holder, pairs)
            if analysis._startup_candle != manifest['expected_native_startups']:
                raise ValueError('Unexpected native startup group; frozen CLI group unchanged')
            frame = holder.indicators[spec['pair']]
            record.update(rows=len(frame), data_start=frame.date.min().isoformat(), data_end=frame.date.max().isoformat(),
                          strategy_startup=active_strategy.startup_candle_count,
                          runmode=active_strategy.dp.runmode.value, native_startups=list(analysis._startup_candle))
            cache_before = copy.deepcopy(active_strategy.target_profit_cache.data)
            # Deep copied dataframe is never returned to the native analysis.
            observed = active_strategy.ft_advise_signals(frame.copy(deep=True), {'pair': spec['pair']})
            if cache_before != active_strategy.target_profit_cache.data:
                raise ValueError('Signal observation unexpectedly changed profit cache')
            if 'enter_long' not in observed or 'exit_long' not in observed:
                raise ValueError('Native long signal columns missing')
            selected = observed.loc[observed.date.isin(checkpoints)]
            snapshots = json.loads(selected.to_json(orient='records', date_format='iso'))
            snapshot_path = output / f'phase-{number:02d}-checkpoint-values.json'
            save(snapshot_path, snapshots)
            signals = []
            for row in snapshots:
                signals.append({'date': pd.Timestamp(row['date']).isoformat(),
                    'raw': {name: row.get(name) for name in signal_fields},
                    'column_present': {name: name in observed.columns for name in signal_fields},
                    'booleans': {name: row.get(name) == 1 for name in signal_fields},
                    'enter_tag': row.get('enter_tag'), 'exit_tag': row.get('exit_tag')})
            record.update(state='COMPLETED', snapshots=snapshot_path.name, snapshot_sha256=sha(snapshot_path),
                          signals=signals, native_final_row_datetime=frame.iloc[-1]['date'].isoformat(),
                          custom_exit_evaluated=False, signal_passes=1)
        except BaseException as exc:
            record.update(state='FAILED', exception_type=type(exc).__name__, exception=str(exc))
            raise
        finally:
            analysis.local_config['user_data_dir'] = previous_userdir
            save(output / 'phases.json', records)

    def observed_analyze(analysis):
        nonlocal native_indicator_report
        original_analyze(analysis)
        # Native report may contain NaN; pandas normalizes it to JSON null.
        native_indicator_report = json.loads(pd.DataFrame(analysis.dict_recursive).to_json())
        save(output / 'native-indicator-variance.json', native_indicator_report)

    StrategyResolver.load_strategy = staticmethod(observed_load)
    RecursiveAnalysis.prepare_data = observed_prepare
    RecursiveAnalysis.analyze_indicators = observed_analyze
    Backtesting.backtest = no_trade_backtest
    command = ['recursive-analysis', '--config', str(config_path), '--strategy-path',
        str((ROOT / manifest['strategy']['path']).parent), '--strategy', 'NostalgiaForInfinityX8',
        '--datadir', str(ROOT / spec['datadir']), '--userdir', str(output / 'utility-userdir'),
        '--timerange', manifest['timerange'], '--pairs', spec['pair'], '--startup-candle',
        *map(str, manifest['startup_candles'])]
    evidence = {'schema_version': 1, 'case': key, 'state': 'RUNNING', 'orders_enabled': False,
        'official_command_count': 1, 'actual_financial_backtests': 0,
        'runner_sha256': sha(Path(__file__)), 'manifest_sha256': sha(MANIFEST), 'command': command,
        'environment_result': env_result, 'started_utc': datetime.now(UTC).isoformat(),
        'config_differences': {k: {'before': config.get(k), 'after': v} for k, v in sanitized.items() if config.get(k) != v},
        'scope': 'read-only signal-column observations on copies of native recursive indicator frames; callbacks and strategy acceptance untested'}
    save(output / 'execution.json', evidence)
    exit_code = 0
    try:
        with resource_watch(output, manifest):
            freqtrade_main(command)
        evidence['state'] = 'COMPLETED'
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
        evidence['state'] = 'COMPLETED' if exit_code == 0 else 'FAILED'
    except BaseException as exc:
        exit_code = 1
        evidence.update(state='FAILED', exception_type=type(exc).__name__, exception=str(exc))
        raise
    finally:
        # freqtrade.main always raises SystemExit, including code=None for some
        # handled failures. Validate completion on the shared finalization path.
        if evidence['state'] == 'COMPLETED' and (len(records) != manifest['maximum_indicator_preparations_per_command'] or any(r['state'] != 'COMPLETED' for r in records)):
            exit_code = 1
            evidence.update(state='FAILED', exception_type='IncompleteDiagnostic',
                exception='Official recursive command did not complete all seven registered preparations')
        StrategyResolver.load_strategy = staticmethod(original_load)
        RecursiveAnalysis.prepare_data = original_prepare
        RecursiveAnalysis.analyze_indicators = original_analyze
        Backtesting.backtest = original_backtest
        restore_exchange()
        comparisons = []
        baseline = {x['date']: x for x in records[0].get('signals', [])} if records else {}
        for record in records[1:]:
            dates = manifest['primary_recursive_checkpoints'] if record['kind'] == 'recursive' else manifest['early_lookahead_checkpoints']
            bydate = {x['date']: x for x in record.get('signals', [])}
            for date in dates:
                left, right = baseline.get(date), bydate.get(date)
                comparisons.append({'kind': record['kind'], 'startup': record['requested_startup'],
                    'date': date, 'both_available': left is not None and right is not None,
                    'boolean_signals_equal': left is not None and right is not None and left['booleans'] == right['booleans'],
                    'tags_equal': left is not None and right is not None and all(left[k] == right[k] for k in ('enter_tag', 'exit_tag')),
                    'baseline': left, 'partial': right})
        save(output / 'signal-comparisons.json', comparisons)
        evidence.update(exit_code=exit_code, elapsed_seconds=time.monotonic()-started,
            indicator_preparations_started=len(records), indicator_preparations_completed=sum(r['state']=='COMPLETED' for r in records),
            signal_observation_passes=sum(r.get('signal_passes', 0) for r in records),
            required_signal_comparisons=len(comparisons),
            all_observed_boolean_signals_equal=bool(comparisons) and all(r['boolean_signals_equal'] for r in comparisons),
            all_observed_tags_equal=bool(comparisons) and all(r['tags_equal'] for r in comparisons),
            strategy_accepted=False, engine_and_inputs_reverified=False)
        try:
            verify(manifest, key)
            evidence['engine_and_inputs_reverified'] = True
        finally:
            save(output / 'execution.json', evidence)
    print(json.dumps({'case': key, 'state': evidence['state'], 'indicator_preparations': len(records), 'financial_backtests': 0}))
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=[m+'_'+a for m in ('spot', 'futures') for a in ('BTC','ETH','ADA')], required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    guard, result, config = verify(manifest, args.case)
    if not args.execute:
        print(json.dumps({'state': 'PREFLIGHT_ONLY', 'case': args.case, 'new_diagnostic_invocations': 0, 'manifest_sha256': sha(MANIFEST)}))
        return 0
    with exclusive_worker(manifest):
        return execute(manifest, args.case, guard, result, config)


if __name__ == '__main__':
    raise SystemExit(main())
