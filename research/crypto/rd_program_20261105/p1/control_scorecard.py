"""Read-only statistics over completed cash/BTC controls; never runs an account.

Only this script's derived control_scorecard.json is written. Frozen sources,
registration manifests, account outputs and their original verdicts are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import tempfile
import os

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def number(value):
    return float(value) if np.isfinite(value) else None


def drawdown_stats(series):
    """Durations run from the latest prior HWM to recovery, or censored endpoint."""
    values = series.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(values)
    dd = 1 - values / peaks
    trough = int(np.argmax(dd))
    peak = int(np.flatnonzero(values[:trough + 1] == peaks[trough])[-1])
    future = np.flatnonzero(values[trough + 1:] >= peaks[trough])
    recovery = trough + 1 + int(future[0]) if len(future) else None
    longest_hours, longest_censored = 0.0, False
    last_peak, active_start = 0, None
    for i, (value, high) in enumerate(zip(values, peaks)):
        if value >= high:
            if active_start is not None:
                hours = (series.index[i] - series.index[active_start]).total_seconds() / 3600
                if hours > longest_hours:
                    longest_hours, longest_censored = hours, False
            active_start, last_peak = None, i
        elif active_start is None:
            active_start = last_peak
    if active_start is not None:
        hours = (series.index[-1] - series.index[active_start]).total_seconds() / 3600
        if hours >= longest_hours:
            longest_hours, longest_censored = hours, True
    has_dd = bool(dd[trough] > 0)
    return {
        'mdd': float(dd[trough]),
        'mdd_peak_at': series.index[peak].isoformat() if has_dd else None,
        'mdd_trough_at': series.index[trough].isoformat() if has_dd else None,
        'mdd_recovery_at': series.index[recovery].isoformat() if has_dd and recovery is not None else None,
        'mdd_episode_recovered': bool(recovery is not None) if has_dd else None,
        'mdd_peak_to_trough_hours': (series.index[trough] - series.index[peak]).total_seconds() / 3600 if has_dd else 0.0,
        'longest_underwater_episode_hours': longest_hours,
        'longest_episode_right_censored': longest_censored,
        'terminal_underwater_hours': (series.index[-1] - series.index[active_start]).total_seconds() / 3600 if active_start is not None else 0.0,
        'underwater_snapshot_fraction': float((dd > 0).mean()),
        'observation_resolution_minutes': 5,
    }


def calendar_stats(series):
    midnight = series.loc[(series.index.hour == 0) & (series.index.minute == 0)]
    daily = midnight.pct_change().dropna()
    day_count = len(daily)
    if len(midnight) > 1 and not (midnight.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all():
        raise ValueError('Incomplete UTC midnight calendar')
    monday = midnight.loc[midnight.index.weekday == 0]
    weekly = monday.pct_change().dropna()
    if len(monday) > 1 and not (monday.index.to_series().diff().dropna() == pd.Timedelta(days=7)).all():
        raise ValueError('Incomplete Monday UTC week boundary calendar')
    complete = series.loc[midnight.index[0]:midnight.index[-1]]
    complete_mdd = float((1 - complete / complete.cummax()).max())
    cagr = (float(midnight.iloc[-1] / midnight.iloc[0]) ** (365 / day_count) - 1) if day_count and midnight.iloc[0] > 0 and midnight.iloc[-1] > 0 else None
    sd = daily.std(ddof=1) if day_count >= 2 else np.nan
    downside = np.sqrt(np.mean(np.minimum(daily, 0) ** 2)) if day_count else np.nan
    q05 = daily.quantile(.05) if day_count else np.nan
    tail = daily.loc[daily <= q05] if day_count else daily
    positive_pnl = midnight.diff().dropna().clip(lower=0)
    positive_sum = float(positive_pnl.sum())
    worst_day_end = daily.idxmin() if day_count else None
    worst_week_end = weekly.idxmin() if len(weekly) else None
    return {
        'complete_day_count': day_count,
        'complete_daily_interval_start': midnight.index[0].isoformat(),
        'complete_daily_interval_end': midnight.index[-1].isoformat(),
        'final_partial_day_excluded_from_daily_and_annualized_metrics': series.index[-1] > midnight.index[-1],
        'final_partial_day_start': midnight.index[-1].isoformat() if series.index[-1] > midnight.index[-1] else None,
        'complete_week_count': len(weekly),
        'week_definition': 'Monday00:00 UTC to next Monday00:00 UTC; both endpoints observed',
        'worst_complete_day_return': float(daily.min()) if day_count else None,
        'worst_complete_day_start': (worst_day_end - pd.Timedelta(days=1)).isoformat() if worst_day_end is not None else None,
        'worst_complete_day_end': worst_day_end.isoformat() if worst_day_end is not None else None,
        'worst_complete_week_return': float(weekly.min()) if len(weekly) else None,
        'worst_complete_week_start': (worst_week_end - pd.Timedelta(days=7)).isoformat() if worst_week_end is not None else None,
        'worst_complete_week_end': worst_week_end.isoformat() if worst_week_end is not None else None,
        'daily_ES95_mean_return': float(tail.mean()) if len(tail) else None,
        'ES95_observed_tail_days': len(tail),
        'daily_NAV_Sharpe_365_ddof1_rf0': number(daily.mean() / sd * np.sqrt(365)) if sd > 0 else None,
        'daily_Sortino_365_target0_all_day_downside': number(daily.mean() / downside * np.sqrt(365)) if downside > 0 else None,
        'complete_days_geometric_annualized_return': cagr,
        'complete_days_five_minute_reference_mdd': complete_mdd,
        'Calmar_complete_days_CAGR_over_same_interval_5m_reference_MDD': cagr / complete_mdd if cagr is not None and complete_mdd > 0 else None,
        'max_complete_day_positive_pnl_contribution_share': float(positive_pnl.max() / positive_sum) if positive_sum > 0 else None,
        'annualization_is_descriptive_not_forecast': True,
        'less_than_one_year_of_complete_days': day_count < 365,
        'small_sample_warning': 'Only12 complete daily returns and1 complete UTC week; annualized ratios/ES are unstable descriptive statistics.' if day_count == 12 else 'Retrospectively observed dependent daily returns; annualization is not independent performance evidence.',
    }


def check_receipts(manifest_path, receipt):
    manifest = read(manifest_path)
    output = ROOT / manifest['isolated_output_path']
    result = read(output / 'result.json')
    if sha(manifest_path) != result['manifest_sha256'] or sha(manifest_path) != receipt['manifest_sha256']:
        raise ValueError('Manifest identity drift')
    if sha(output / 'result.json') != receipt['result_sha256']:
        raise ValueError('Result identity drift')
    curve_hash = sha(output / 'reference_equity.parquet')
    if curve_hash != result['output_sha256'] or curve_hash != receipt['output_sha256']:
        raise ValueError('Curve identity drift')
    for relative, expected in manifest['source_hashes'].items():
        if sha(ROOT / relative) != expected:
            raise ValueError('Frozen account runner drift')
    for rec in manifest['data_receipt_hashes'].values():
        if sha(ROOT / rec['path']) != rec['sha256']:
            raise ValueError('Frozen raw input drift')
    frame = pd.read_parquet(output / 'reference_equity.parquet').set_index('date')
    expected = pd.date_range(manifest['window']['start'], pd.Timestamp(manifest['window']['end_exclusive']) - pd.Timedelta(minutes=5), freq='5min')
    if not frame.index.equals(expected) or not np.isfinite(frame.reference_equity).all() or (frame.reference_equity <= 0).any():
        raise ValueError('Invalid existing curve calendar or NAV')
    return manifest, output, result, frame


def score(manifest_path, receipt):
    manifest, output, result, frame = check_receipts(manifest_path, receipt)
    series = frame.reference_equity
    cash_control = manifest['baseline_id'] == 'B0_CASH'
    cost = manifest['cost_scenario']
    total_rate = D(str(cost['total_bps_per_side'])) / 10000
    commission_rate = D(str(cost['fee_bps_per_side'])) / 10000
    proxy_rate = D(str(cost['execution_proxy_bps_per_side'])) / 10000
    quantity = D(str(result['quantity']))
    entry_notional = entry_fee = terminal_notional = funding = D(0)
    source_events = 0
    funding_exact = None
    if not cash_control:
        prices = pd.read_feather(ROOT / manifest['data_receipt_hashes']['prices']['path']).set_index('date').open
        entry_time = pd.Timestamp(result['entry_time'])
        entry = D(str(prices.loc[entry_time]))
        terminal = D(str(prices.loc[frame.index[-1]]))
        entry_notional, terminal_notional = quantity * entry, quantity * terminal
        entry_fee = entry_notional * total_rate
        if entry_notional + entry_fee > 990:
            raise ValueError('99 percent inclusive-entry-cost budget exceeded')
        if manifest['market'] == 'futures':
            rates = pd.read_feather(ROOT / manifest['data_receipt_hashes']['funding']['path']).set_index('date').funding_rate
            marks = pd.read_feather(ROOT / manifest['data_receipt_hashes']['marks']['path']).set_index('date').open
            if rates.index.has_duplicates or not rates.index.is_monotonic_increasing or not np.isfinite(rates).all() or not np.isfinite(marks).all() or (marks <= 0).any():
                raise ValueError('Invalid source funding/marks')
            selected = rates.loc[(rates.index > entry_time) & (rates.index < frame.index[-1])]
            observed = read(output / 'funding_events.json')
            if len(selected) != len(observed):
                raise ValueError('Funding event omitted or duplicated')
            source_events = len(selected)
            funding_exact = True
            for (at, rate), event in zip(selected.items(), observed):
                if at != pd.Timestamp(event['time']) or D(str(rate)) != D(str(event['rate'])) or D(str(marks.loc[at])) != D(str(event['mark'])):
                    raise ValueError('Funding event differs from source')
                funding -= quantity * D(str(marks.loc[at])) * D(str(rate))
        expected = D(1000) + quantity * (terminal - entry) - entry_fee - terminal_notional * total_rate + funding
    else:
        expected = D(1000)
        if not (series == 1000).all() or not (frame.gross_notional == 0).all():
            raise ValueError('Cash control ceased being cash')
    error = abs(expected - D(str(series.iloc[-1])))
    if error > D('.00000001') or abs(funding - D(str(result['funding_net_usdt']))) > D('.00000001'):
        raise ValueError('Independent source terminal/funding identity mismatch')
    dd = drawdown_stats(series)
    if abs(dd['mdd'] - result['metrics']['reference_mdd']) > 1e-12:
        raise ValueError('MDD differs from original result')
    calendar = calendar_stats(series)
    for new, old in [('daily_NAV_Sharpe_365_ddof1_rf0', 'daily_NAV_Sharpe'), ('daily_Sortino_365_target0_all_day_downside', 'daily_Sortino')]:
        a, b = calendar[new], result['metrics'][old]
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-10):
            raise ValueError('Daily ratio differs from original result')
    net_pnl = float(series.iloc[-1] - 1000)
    gross_fraction = frame.gross_notional / series
    span_hours = (series.index[-1] - series.index[0]).total_seconds() / 3600
    holding_hours = max(0, (series.index[-1] - pd.Timestamp(result['entry_time'])).total_seconds() / 3600) if not cash_control else 0
    last_hour = frame.index[-1].floor('h')
    return {
        'id': result['id'], 'baseline': manifest['baseline_id'], 'market': manifest['market'],
        'window': manifest['window_id'], 'cost_id': manifest['cost_id'],
        'evidence_class': manifest['window']['evidence_class'],
        'source_receipts': {'manifest_sha256': sha(manifest_path), 'result_sha256': sha(output / 'result.json'), 'curve_sha256': sha(output / 'reference_equity.parquet')},
        'initial_equity_usdt': 1000, 'terminal_reference_equity_usdt': float(series.iloc[-1]),
        'net_reference_pnl_usdt': net_pnl, 'net_reference_return': net_pnl / 1000,
        'valuation': result['reference_valuation'], 'five_minute_executable_or_futures_mark_verified': False,
        'reference_initial_500_floor_breached': bool((series <= 500).any()),
        'drawdown': dd, 'calendar_metrics': calendar,
        'budget_and_cost': {
            'entry_budget_max_usdt_including_entry_cost': 990 if not cash_control else 0,
            'actual_entry_notional_usdt': float(entry_notional),
            'actual_entry_cost_inclusive_budget_used_usdt': float(entry_notional + entry_fee),
            'actual_budget_use_fraction_of_initial_equity': float((entry_notional + entry_fee) / 1000),
            'unspent_initial_cash_after_entry_usdt': float(D(1000) - entry_notional - entry_fee) if manifest['market'] == 'spot' else None,
            'unallocated_initial_wallet_after_entry_margin_and_cost_usdt': float(D(1000) - entry_notional - entry_fee) if manifest['market'] == 'futures' else None,
            'fee_bps_per_side': cost['fee_bps_per_side'], 'execution_proxy_bps_per_side': cost['execution_proxy_bps_per_side'],
            'modeled_entry_commission_usdt': float(entry_notional * commission_rate),
            'modeled_entry_execution_proxy_usdt': float(entry_notional * proxy_rate),
            'modeled_entry_total_cost_usdt': float(entry_fee),
            'terminal_hypothetical_exit_commission_reserve_usdt': float(terminal_notional * commission_rate),
            'terminal_hypothetical_exit_execution_proxy_reserve_usdt': float(terminal_notional * proxy_rate),
            'terminal_hypothetical_exit_total_reserve_usdt': float(terminal_notional * total_rate),
            'entry_plus_hypothetical_exit_cost_usdt': float(entry_fee + terminal_notional * total_rate),
            'model_funding_cash_net_usdt': float(funding), 'source_funding_events_included': source_events,
            'funding_source_membership_and_rates_verified': funding_exact,
            'funding_convention': ('Observed historical rates times hourly mark OPEN, long payment negative; modeled cost, not authenticated customer settlement.' if manifest['market'] == 'futures' and not cash_control else 'Not applicable: no modeled funding cash flow for this control.'),
            'actual_exchange_fees_or_settlements_verified': False,
            'initial_budget_note': ('99% maximum inclusive-entry-cost budget; actual exposure is lower after archived step rounding, especially futures L (~937 USDT used).' if not cash_control else 'Zero-order cash control; all1000 USDT remain cash, no entry budget used.'),
        },
        'exposure_and_turnover': {
            'direction': 'flat' if cash_control else 'long_only',
            'observed_snapshot_mean_gross_equity_fraction': float(gross_fraction.mean()),
            'observed_snapshot_max_gross_equity_fraction': float(gross_fraction.max()),
            'observed_snapshot_mean_net_equity_fraction': float(gross_fraction.mean()),
            'observed_snapshot_max_abs_net_equity_fraction': float(gross_fraction.max()),
            'terminal_gross_notional_usdt': float(frame.gross_notional.iloc[-1]),
            'terminal_net_notional_usdt': float(frame.gross_notional.iloc[-1]),
            'position_present_snapshot_fraction': float((frame.gross_notional > 0).mean()),
            'entry_event_to_valuation_endpoint_holding_hours': holding_hours,
            'valuation_span_hours': span_hours,
            'event_defined_time_in_market_fraction': holding_hours / span_hours if span_hours > 0 else None,
            'modeled_filled_one_way_turnover_over_initial_equity': float(entry_notional / 1000),
            'hypothetical_terminal_exit_turnover_not_counted_as_fill': float(terminal_notional / 1000),
            'snapshot_averages_are_not_continuous_intrabar_exposure': True,
        },
        'concentration_and_capacity': {
            'BTC_share_of_single_asset_gross_exposure': 1.0 if not cash_control else None,
            'BTC_share_of_positive_net_pair_contributions': 1.0 if not cash_control and net_pnl > 0 else None,
            'positive_pair_contribution_denominator_usdt': max(net_pnl, 0) if not cash_control else 0,
            'positive_contribution_note': 'Null when no positive net pair contribution; losing BTC-only accounts still have100% BTC exposure.',
            'capacity': 'unknown_no_historical_depth_queue_or_market_impact_evidence',
        },
        'endpoints_and_campaigns': {
            'old_hourly_reference_anchor': last_hour.isoformat(),
            'old_hourly_anchor_reference_equity': float(series.loc[last_hour]),
            'new_five_minute_reference_endpoint': series.index[-1].isoformat(),
            'endpoint_reference_equity_difference_usdt': float(series.iloc[-1] - series.loc[last_hour]),
            'hourly_actual_mark_end_time': result.get('hourly_mark_end_time'),
            'hourly_actual_mark_end_equity': result.get('hourly_mark_end_equity'),
            'hourly_actual_mark_mdd': result.get('hourly_mark_mdd'),
            'natural_complete_campaigns': result['natural_complete_campaigns'],
            'terminal_open_positions': result['terminal_open_positions'],
            'terminal_reserve_is_not_a_filled_exit': not cash_control,
        },
        'read_only_audit': {'all_original_hashes_valid': True, 'independent_source_terminal_error_usdt': str(error)},
        'economic_acceptance': result['economic_acceptance'], 'RD1_applied': False,
        'orders_enabled': False, 'production_eligible': False,
    }


def self_check():
    index = pd.date_range('2026-01-05T00:00:00Z', periods=5, freq='5min')
    a = drawdown_stats(pd.Series([100, 90, 100, 80, 70], index=index))
    assert abs(a['mdd'] - .3) < 1e-12
    assert a['longest_underwater_episode_hours'] == 10 / 60
    assert a['longest_episode_right_censored']
    assert a['terminal_underwater_hours'] == 10 / 60
    b = drawdown_stats(pd.Series([100] * 5, index=index))
    assert b['mdd'] == 0 and b['mdd_peak_at'] is None and b['longest_underwater_episode_hours'] == 0
    index = pd.date_range('2026-01-05T00:00:00Z', periods=8 * 288, freq='5min')
    c = calendar_stats(pd.Series(100., index=index))
    assert c['complete_day_count'] == 7 and c['complete_week_count'] == 1
    assert c['daily_NAV_Sharpe_365_ddof1_rf0'] is None
    assert c['Calmar_complete_days_CAGR_over_same_interval_5m_reference_MDD'] is None
    assert c['final_partial_day_excluded_from_daily_and_annualized_metrics']


def main():
    self_check()
    original = read(HERE / 'control_results.json')
    if len(original['rows']) != 16 or len({r['id'] for r in original['rows']}) != 16:
        raise ValueError('Missing or duplicated frozen control cells')
    rows = [score(HERE / 'manifests' / (r['id'] + '.json'), r) for r in original['rows']]
    comparisons = []
    for baseline in ['B0_CASH', 'B1_BTC_HOLD']:
        for market in ['spot', 'futures']:
            for window in ['L', 'S']:
                group = {r['cost_id']: r for r in rows if (r['baseline'], r['market'], r['window']) == (baseline, market, window)}
                delta = group['stress']['net_reference_pnl_usdt'] - group['base']['net_reference_pnl_usdt']
                if delta > 1e-8:
                    raise ValueError('Unexpected positive stress-cost terminal difference')
                comparisons.append({'baseline': baseline, 'market': market, 'window': window,
                                    'stress_minus_base_terminal_reference_pnl_usdt': delta,
                                    'interpretation': 'Same capital/budget; quantity can change with cost-inclusive lot rounding, so not a fixed-quantity fee derivative.'})
    report = {
        'schema_version': 1, 'artifact': 'P1_read_only_control_scorecard',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'rows': rows,
        'count': len(rows), 'financial_accounts_executed_by_this_script': 0,
        'source_sha256': sha(__file__), 'input_control_results_sha256': sha(HERE / 'control_results.json'),
        'frozen_plan_sha256': sha(HERE.parent / 'plan.json'),
        'original_artifacts_modified': False, 'synthetic_statistic_self_checks_passed': True,
        'paired_cost_comparisons': comparisons,
        'definitions': {
            'return_and_full_mdd': 'All existing reference snapshots from window start through end_exclusive minus5minutes; hypothetical remaining exit cost already reserved.',
            'drawdown_duration': 'Latest preceding observed high-water timestamp to recovery or last observed endpoint; unrecovered episodes right-censored; not tick-level duration.',
            'daily': 'Consecutive observed UTC00 snapshots only; final00:00-to23:55 partial day excluded from ratios/CAGR/Calmar.',
            'weekly': 'Complete Monday00-to-Monday00 UTC intervals only, no partial boundary week.',
            'calmar': 'Geometric365-day annualized growth from first/last complete daily endpoints divided by5-minute reference MDD within those same endpoints. Zero MDD =>null.',
            'fees': 'Frozen commission plus execution proxy, applied once at entry; terminal exit cost is a hypothetical reserve, not a natural exit.',
            'funding': 'Actual historical rate inputs with hourly mark OPEN modeling; not authenticated customer settlement or forward-known features.',
            'capacity': 'Unknown; no historical depth/queue/impact inference.',
        },
        'limitations': ['All windows were historically observed; no independent OOS or profitability qualification.',
                        '5m OPEN reference is not executable bid/ask or verified5m futures mark.',
                        'Short window has12 complete daily returns and1 complete UTC week; annualization is descriptive and unstable.',
                        'Cash has undefined risk ratios when denominators are zero; BTC losing net contribution concentration is null, not zero diversification.',
                        'No risk module activation or new strategy/account run; current paper services are unchanged.'],
        'orders_enabled': False, 'production_eligible': False,
    }
    destination = HERE / 'control_scorecard.json'
    fd, temporary = tempfile.mkstemp(prefix='.control_scorecard-', dir=HERE)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({'derived_rows': len(rows), 'financial_runs': 0, 'all_original_receipts_valid': True,
                      'max_source_terminal_error_usdt': str(max(D(r['read_only_audit']['independent_source_terminal_error_usdt']) for r in rows))}))


if __name__ == '__main__':
    main()
