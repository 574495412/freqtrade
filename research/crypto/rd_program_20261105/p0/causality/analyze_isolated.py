#!/usr/bin/env python3
"""Read-only post-run audit. No engine import, diagnostic or account replay."""
from __future__ import annotations
import argparse
import copy
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dt(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def normalized(value):
    # Engine counters differ for single-pair slice. All prices, amounts, times,
    # fees, cache contents, callback results, directions and state are retained.
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in {'id', 'order_id', 'sequence', 'phase'}}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def matching(trades, target):
    result = [x for x in trades if x['pair'] == target['pair'] and dt(x['open_date']) == dt(target['open_date'])]
    if len(result) != 1:
        raise ValueError('Target trade identity is missing or ambiguous')
    return result[0]


def normalize_grind_order_id_suffix(trade):
    result = copy.deepcopy(trade)
    for order in result['orders']:
        tag = order.get('ft_order_tag')
        if isinstance(tag, str) and re.fullmatch(r'grind_[145]_exit(?: \d+)+', tag):
            order['ft_order_tag'] = tag.split()[0] + ' <order-id-suffix>'
    return result


def analyze():
    inputs = set()
    def read(path):
        inputs.add(path)
        return json.loads(path.read_text())
    manifest = read(HERE / 'diagnostic_manifest.json')
    saved = read(HERE / 'saved_evidence.json')
    old_futures = read(ROOT / 'user_data/crypto_quant/nfi_x8_lookahead_inspect_20261105_v002/captured-slices.json')
    result = {'schema_version': 1, 'orders_enabled': False, 'new_financial_runs': 0,
        'script_sha256': sha(Path(__file__)), 'markets': {}, 'alerts': [],
        'decision': 'The registered cache-isolated diagnostics remove the six historical exit alerts in the exercised sample. Evidence identifies an official utility orchestration/cache lifecycle mechanism; it does not certify normal-run global causality or strategy acceptance.'}
    for market, spec in manifest['markets'].items():
        output = ROOT / spec['output_directory']
        execution = read(output / 'execution.json')
        calls = read(output / 'phase-calls.json')
        baseline = read(output / calls[0]['trade_file'])
        csvpath = output / 'lookahead-result.csv'
        inputs.add(csvpath)
        with csvpath.open() as stream:
            csv_rows = list(csv.DictReader(stream))
        if len(csv_rows) != 1:
            raise ValueError('Expected exactly one official strategy diagnostic row')
        official = csv_rows[0]
        expected_signals = 12 if market == 'futures' else 11
        expected_calls = 1 + expected_signals * 2
        assert execution['state'] == 'COMPLETED' and execution['exit_code'] == 0
        assert execution['engine_and_inputs_reverified'] is True
        assert execution['actual_internal_backtests_started'] == execution['actual_internal_backtests_completed'] == expected_calls
        assert len(calls) == expected_calls and all(x['state'] == 'COMPLETED' for x in calls)
        assert all(x['cache_at_load'] == {} and x['runmode'] == 'util_no_exchange' for x in calls)
        assert len({x['user_data_dir'] for x in calls}) == expected_calls
        assert int(official['total_signals']) == expected_signals
        assert int(official['biased_entry_signals']) == int(official['biased_exit_signals']) == 0
        assert execution['future_cache_reads'] == 0
        assert sha(csvpath) == execution['official_csv_sha256']
        assert sha(output / 'phase-calls.json') == execution['phase_calls_sha256']
        tracepath = output / 'callback-trace.jsonl'
        inputs.add(tracepath)
        assert sha(tracepath) == execution['callback_trace_sha256']
        events = [json.loads(x) for x in tracepath.read_text().splitlines()]
        assert len(events) == execution['callback_trace_events']
        assert not any(x.get('cache_from_future') for x in events)
        all_trades = {}
        for phase in calls:
            path = output / phase['trade_file']
            assert sha(path) == phase['trade_sha256']
            all_trades[phase['id']] = read(path)
        oldpath = ROOT / spec['historical_comparator']
        oldcachefiles = list(oldpath.glob('nfix8-profit_max*.json'))
        newcachefiles = list((output / calls[0]['user_data_dir']).glob('nfix8-profit_max*.json'))
        assert len(oldcachefiles) == len(newcachefiles) == 1
        oldcache, newcache = read(oldcachefiles[0]), read(newcachefiles[0])
        assert oldcache == newcache
        market_result = {'execution': execution, 'official_csv': official,
            'all_phases_started_empty_cache': True, 'distinct_phase_directories': expected_calls,
            'baseline_trade_count': len(baseline), 'baseline_final_cache_exactly_equals_old_final_cache': True,
            'baseline_final_cache': newcache, 'future_cache_reads': 0,
            'trace_normalization_ignored_fields': ['id', 'order_id', 'sequence', 'phase']}
        identity_checks = []
        for phase in calls:
            if phase['kind'] != 'exit':
                continue
            originals = [t for t in baseline if t['pair'] == phase['pairs'][0] and dt(t['close_date']) == dt(phase['compared_dt'])]
            assert len(originals) == 1
            target_trade = matching(all_trades[phase['id']], originals[0])
            identity_checks.append({'phase': phase['id'], 'pair': originals[0]['pair'],
                'open_date': originals[0]['open_date'], 'close_date': originals[0]['close_date'],
                'whole_target_trade_exact_equal': target_trade == originals[0],
                'equal_after_grind_order_id_suffix_normalization': normalize_grind_order_id_suffix(target_trade) == normalize_grind_order_id_suffix(originals[0]),
                'literal_order_tag_differences': [{'order_index': i, 'baseline': a['ft_order_tag'], 'slice': b['ft_order_tag']}
                    for i, (a, b) in enumerate(zip(originals[0]['orders'], target_trade['orders'])) if a['ft_order_tag'] != b['ft_order_tag']]})
        assert len(identity_checks) == expected_signals and all(x['equal_after_grind_order_id_suffix_normalization'] for x in identity_checks)
        market_result['all_checked_clean_target_trade_parity'] = identity_checks
        market_result['grind_tag_normalization_source'] = {'path': manifest['strategy_path'],
            'lines': [36960, 37356, 37494], 'rule': 'Only integer suffixes of grind_1/4/5_exit; original source appends grind entry order IDs. Literal differences retained; prices/times/quantities/funding and all other fields stay exact.'}
        if market == 'futures':
            oldbaseline = [x['baseline'] for x in old_futures]
            assert len(oldbaseline) == len(baseline) == 12 and oldbaseline == baseline
            market_result['old_baseline_parity'] = {'scope': 'all 12 archived baseline trades; every exported field including all orders, prices, amounts, tags and funding', 'exact_equal': True}
            phase_comparisons = []
            for capture in old_futures:
                for kind in ['entry', 'exit']:
                    phase_id = 1 + 2 * capture['index'] + (kind == 'exit')
                    oldtrades = capture['slices'][kind]['trades']
                    newtrades = all_trades[phase_id]
                    phase_comparisons.append({'baseline_index': capture['index'], 'kind': kind,
                        'old_trade_count': len(oldtrades), 'new_trade_count': len(newtrades),
                        'entire_slice_exact_equal': oldtrades == newtrades})
            market_result['all_24_old_new_slice_comparisons'] = phase_comparisons
        else:
            logpath = oldpath / 'lookahead.log'
            inputs.add(logpath)
            log = logpath.read_text()
            groups = re.split(r'freqtrade.optimize.backtesting - INFO - Loading data from ', log)[1:]
            logged_phases = []
            for chunk in groups:
                end = re.search(r'up to (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', chunk)
                pair = re.search(r'Loading data for ((?:ADA|ETH)/USDT) ', chunk)
                if end:
                    logged_phases.append({'to_dt': end.group(1).replace(' ', 'T')+'+00:00', 'pair': pair.group(1) if pair else None})
            assert len(logged_phases) == expected_calls
            comparisons = [{'phase': c['id'], 'old': old, 'new_to_dt': c['to_dt'],
                'cutoff_equal': old['to_dt'] == c['to_dt'],
                'pair_equal': c['id'] == 0 or old['pair'] == c['pairs'][0]}
                for old, c in zip(logged_phases, calls)]
            assert all(x['cutoff_equal'] and x['pair_equal'] for x in comparisons)
            market_result['old_baseline_parity'] = {'scope': 'all 11 checked trade pair/open/close identities inferred from 22 logged slice cutoff/pair sequences; reported baseline count 12; complete old trade/order exports absent',
                'all_logged_phase_sequences_equal': True, 'full_trade_order_funding_parity': None,
                'missing': 'Original spot full baseline and per-slice trade exports were not saved', 'phase_comparisons': comparisons}
        for target in spec['alerts']:
            original_alert = next(x for x in saved['alerts'] if x['market'] == market and x['pair'] == target['pair'] and dt(x['open_date']) == dt(target['open_date']))
            base_trade = matching(baseline, target)
            phase = next(x for x in calls if x['kind'] == 'exit' and x['pairs'] == [target['pair']] and x['compared_dt'] == target['baseline_close_date'])
            clean_trade = matching(all_trades[phase['id']], target)
            assert base_trade == clean_trade
            def target_events(number):
                out = []
                for e in events:
                    trade = e.get('trade', e.get('trade_before'))
                    if e['phase'] == number and trade and trade['pair'] == target['pair'] and trade['open_date'] == target['open_date']:
                        out.append(e)
                return out
            base_events, clean_events = target_events(0), target_events(phase['id'])
            assert base_events and normalized(base_events) == normalized(clean_events)
            closing_callbacks = [e for e in clean_events if e['event'] == 'order_filled' and dt(e['time']) == dt(clean_trade['close_date'])]
            assert len(closing_callbacks) == 1
            close = closing_callbacks[0]
            assert close['recognized_as_backtest'] is False and close['trade_before']['is_open'] is True
            assert close['cache_before'] == close['cache_after']
            first_exit = next(e for e in clean_events if e['event'] == 'custom_exit')
            cache_peak = oldcache[target['pair']]
            record = {'id': original_alert['id'], 'market': market, 'target': target,
                'old_alert': original_alert, 'clean_exit_slice_phase': phase['id'],
                'clean_trade_exactly_matches_new_baseline_trade': True,
                'clean_trade': clean_trade, 'clean_callback_trace_exactly_matches_baseline': True,
                'compared_callback_events_per_phase': len(clean_events),
                'first_clean_custom_exit': first_exit,
                'closing_callback': close,
                'old_final_cache_reproduced_by_clean_baseline': cache_peak,
                'old_final_cache_peak_is_future_to_first_slice_callback': dt(cache_peak['time_profit_reached']) > dt(first_exit['time']),
                'diagnostic_alert_reproduced_after_isolation': False,
                'not_proven': ['Exact old cache bytes at each historical callback were not captured', 'Normal backtest/live path global absence of future-data use', 'Other pairs/timeframes/exits/DCA and unseen periods']}
            if market == 'futures':
                early_time = dt(original_alert['slice_close_date'])
                clean_at_old_exit = [e for e in clean_events if e['event'] == 'custom_exit' and dt(e['time']) == early_time]
                assert len(clean_at_old_exit) == 1 and clean_at_old_exit[0]['result'] is None
                record.update(clean_callback_at_old_early_exit=clean_at_old_exit[0],
                    attribution='SUPPORTED_ORCHESTRATION_CACHE_LIFECYCLE: exact full-baseline parity, old final cache regenerated, source-method future-peak counterexample matches old early exit, clean same-time callback returns no exit, complete clean target trace agrees with baseline')
            else:
                record.update(attribution='CONSISTENT_WITH_SAME_ORCHESTRATION_MECHANISM_BUT_HISTORICAL_FIRST_DIVERGENCE_BLOCKED: all logged checked-trade identities and final cache agree; old slice exit/order bytes absent',
                    historical_spot_first_divergence_resolved=False)
            result['alerts'].append(record)
        result['markets'][market] = market_result
    result['budget'] = {'new_official_lookahead_commands_observed': 2,
        'internal_backtests_observed': sum(m['execution']['actual_internal_backtests_started'] for m in result['markets'].values()),
        'additional_dirty_commands': 0, 'analysis_script_backtests': 0}
    result['remaining_gates'] = ['Normal-run callback causality not established by utility-mode diagnostic',
        'Historical spot first-divergence reconstruction unavailable', 'Recursive fixed-checkpoint signals require separate result',
        'Funding same-time sequencing/publication and certified 5m mark NAV remain blocked', 'No strategy/economic/live acceptance']
    result['input_receipts'] = [{'path': str(p.relative_to(ROOT)), 'sha256': sha(p)} for p in sorted(inputs)]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze()
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(args.output), 'sha256': sha(args.output), 'alerts': len(result['alerts']), 'new_backtests': 0}))


if __name__ == '__main__':
    main()
