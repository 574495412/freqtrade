#!/usr/bin/env python3
"""Audit saved recursive outputs only; never imports the trading engine."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()


def diff(left, right):
    numeric, other, missing = [], [], []
    signal_names = {'enter_long', 'enter_short', 'exit_long', 'exit_short', 'enter_tag', 'exit_tag'}
    for key in sorted(set(left) | set(right)):
        if key in signal_names or key == 'date':
            continue
        a, b = left.get(key), right.get(key)
        if a == b:
            continue
        record = {'column': key, 'baseline': a, 'partial': b}
        if a is None or b is None:
            missing.append(record)
        elif isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
            record.update(absolute_difference=abs(b-a), relative_difference=None if a == 0 else (b-a)/abs(a))
            numeric.append(record)
        else:
            other.append(record)
    numeric.sort(key=lambda x: abs(x['relative_difference']) if x['relative_difference'] is not None else math.inf, reverse=True)
    return {'numeric_differing_columns': len(numeric), 'null_mismatches': len(missing), 'other_differing_columns': len(other),
        'largest_relative_differences': numeric[:12], 'all_numeric_differences': numeric,
        'missing_differences': missing, 'other_differences': other,
        'baseline_null_columns': [k for k,v in left.items() if v is None],
        'partial_null_columns': [k for k,v in right.items() if v is None]}


def analyze():
    inputs = set()
    def read(path):
        inputs.add(path)
        return json.loads(path.read_text())
    manifest = read(HERE / 'recursive_manifest.json')
    result = {'schema_version': 1, 'orders_enabled': False, 'script_sha256': sha(Path(__file__)),
        'scope': 'post-run finite checkpoint audit; no new indicators or trading runs', 'cases': [],
        'decision': 'Fixed-checkpoint vector signals and tags are invariant, but all are zero/empty at these checkpoints. Indicator initialization differences remain; activated strategy/callback causality is unproven. No strategy acceptance.'}
    for key, spec in manifest['cases'].items():
        output = ROOT / spec['output_directory']
        execution = read(output / 'execution.json')
        phases = read(output / 'phases.json')
        comparison = read(output / 'signal-comparisons.json')
        native = read(output / 'native-indicator-variance.json')
        resources = read(output / 'resource-usage.json')
        assert execution['state'] == 'COMPLETED' and execution['exit_code'] == 0
        assert execution['actual_financial_backtests'] == 0 and execution['engine_and_inputs_reverified']
        assert execution['manifest_sha256'] == sha(HERE / 'recursive_manifest.json')
        assert execution['runner_sha256'] == manifest['runner_sha256']
        assert execution['indicator_preparations_started'] == execution['indicator_preparations_completed'] == len(phases) == 7
        assert [p['requested_startup'] for p in phases[2:]] == [199, 499, 799, 800, 999]
        assert all(p['state'] == 'COMPLETED' and p['runmode'] == 'util_no_exchange' and p['signal_passes'] == 1 for p in phases)
        assert len(comparison) == 6 and all(c['both_available'] and c['boolean_signals_equal'] and c['tags_equal'] for c in comparison)
        assert resources['state'] == 'FINISHED' and resources['peak_rss_bytes'] < manifest['max_rss_bytes'] and resources['elapsed_seconds'] < manifest['wall_seconds_per_run']
        snapshots = {}
        for phase in phases:
            path = output / phase['snapshots']
            assert sha(path) == phase['snapshot_sha256']
            snapshots[phase['number']] = {date(row['date']): row for row in read(path)}
        all_zero = True
        for phase in phases:
            for sig in phase['signals']:
                assert all(sig['column_present'].values())
                if any(sig['booleans'].values()) or sig['enter_tag'] or sig['exit_tag']:
                    all_zero = False
        comparisons = []
        for phase in phases[1:]:
            selected_date = manifest['early_lookahead_checkpoints'][0] if phase['kind'] == 'native_early_lookahead' else manifest['primary_recursive_checkpoints'][0]
            left, right = snapshots[0][selected_date], snapshots[phase['number']][selected_date]
            comparisons.append({'phase': phase['number'], 'kind': phase['kind'], 'startup': phase['requested_startup'],
                'registered_cli_group': phase['requested_startup'] in manifest['startup_candles'],
                'native_automatic_800_control': phase['requested_startup'] == 800,
                'checkpoint': selected_date, 'difference': diff(left, right)})
        result['cases'].append({'case': key, 'execution': execution, 'resources': resources,
            'native_report_columns_with_variance_at_inclusive_end': len(native),
            'all_observed_checkpoint_signal_columns_zero_and_tags_empty': all_zero,
            'literal_boolean_and_tag_comparisons': comparison,
            'indicator_comparisons_at_frozen_in_window_checkpoints': comparisons,
            'loaded_frames': [{k:p[k] for k in ['number','kind','requested_startup','strategy_startup','rows','data_start','data_end']} for p in phases],
            'callback_or_filled_trade_paths_tested': False})
    result['budget'] = {'official_recursive_commands': 6, 'native_indicator_preparations': 42,
        'additional_signal_observations_on_copies': 42, 'financial_backtests': 0,
        'registered_cli_groups_per_case': [199,499,799,999], 'automatic_native_control_per_case': 800,
        'total_new_diagnostics_including_lookahead': 8, 'remaining_new_diagnostic_invocations': 0}
    result['input_receipts'] = [{'path':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in sorted(inputs)]
    result['limits'] = ['All fixed checkpoint vector signals are zero and tags empty; comparison did not exercise an activated entry/exit decision',
        'NFI custom_exit/DCA callbacks are not vector exit signals and were not invoked in recursive analysis',
        'Native indicator variance output uses inclusive Nov1 00:00; separate audit here uses Oct31 23:55',
        'Snapshot floating values use pandas JSON precision; zero reported difference does not prove bitwise float equality',
        'No further checkpoint tuning, windows or startup experiments authorized after observing these outcomes']
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=analyze()
    with args.output.open('x') as stream:
        json.dump(result,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')
    print(json.dumps({'output':str(args.output),'sha256':sha(args.output),'cases':len(result['cases']),'new_diagnostics':0}))


if __name__=='__main__':
    main()
