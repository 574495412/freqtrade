"""Read actual native orders/NAV; reconcile fills and funding, never simulate orders."""
import gzip
import json
import math
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics, read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import audit_trades, save, sha
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

HERE = Path(__file__).resolve().parent


def require(value, reason):
    if not value:
        raise ValueError(reason)


def inputs(plan, grid):
    result = {}
    for pair in plan['pairs']:
        root = Path(plan['datadir']) / 'futures' / (pair.split('/')[0]+'_USDT_USDT')
        marks = pd.read_feather(str(root)+'-1h-mark.feather').set_index('date').open.reindex(grid)
        require(np.isfinite(marks).all(), 'Missing full mark grid')
        funds = pd.read_feather(str(root)+'-1h-funding_rate.feather')
        funds = funds.loc[funds.date.ge(pd.Timestamp(plan['start'])) & funds.date.lt(pd.Timestamp(plan['end']))]
        rates = funds.funding_rate.to_numpy()
        prices = marks.reindex(pd.DatetimeIndex(funds.date)).to_numpy()
        require(np.isfinite(rates).all() and np.isfinite(prices).all(), 'Incomplete supplied event input')
        result[pair] = {'marks':marks.to_numpy(),
                        'times':pd.DatetimeIndex(funds.date).as_unit('ms').asi8,
                        'unit':rates*prices}
    return result


def ledger(stats, frame, capital, data):
    times = pd.DatetimeIndex(frame.date).as_unit('ms').asi8
    total = np.full(len(times), float(capital))
    counts = np.zeros(len(times), dtype=int)
    gross = np.zeros(len(times))
    signed = np.zeros(len(times))
    ownership = {p:np.zeros(len(x['times']), dtype=int) for p,x in data.items()}
    fund_errors, order_count, multi = [], 0, 0
    unknown_positions = []
    gap = pd.Timestamp('2026-06-24T04:00Z').value//10**6
    for trade in stats['trades']:
        require(trade['leverage'] == 3., 'Unregistered native leverage')
        pair = trade['pair']
        orders = trade['orders']
        order_count += len(orders)
        multi += len(orders) > 2
        filled = np.array([o['order_filled_timestamp'] for o in orders], dtype=np.int64)
        require(np.all(filled[1:] >= filled[:-1]), 'Unordered native fills')
        qty = np.array([o['amount'] if o['ft_is_entry'] else -o['amount'] for o in orders])
        cash = np.array([o['amount']*o['safe_price'] for o in orders])
        entry_cash = np.cumsum([v if o['ft_is_entry'] else 0. for v,o in zip(cash, orders, strict=True)])
        exit_cash = np.cumsum([v if not o['ft_is_entry'] else 0. for v,o in zip(cash, orders, strict=True)])
        quantities = np.cumsum(qty)
        require(abs(quantities[-1]) < 1e-8, 'Terminal quantity imbalance')
        # Snapshot is before all fills/events at the current hour. Actual daily
        # entry/adjustment orders are01:00; supplied events never occur then.
        before = np.searchsorted(filled, times, side='left')
        held = np.concatenate([[0.],quantities])[before]
        held[np.abs(held) < 1e-10] = 0.
        require(held.min() >= -1e-8, 'Negative reconstructed unsigned inventory')
        bought = np.concatenate([[0.],entry_cash])[before]
        sold = np.concatenate([[0.],exit_cash])[before]
        event_times = data[pair]['times']
        active_events = (event_times >= filled[0]) & (event_times <= filled[-1])
        at_event = np.concatenate([[0.],quantities])[
            np.searchsorted(filled, event_times, side='left')]
        at_event = np.where(active_events, at_event, 0.)
        at_event[np.abs(at_event) < 1e-10] = 0.
        sign = -1 if trade['is_short'] else 1
        fees = -sign*at_event*data[pair]['unit']
        expected_funding = math.fsum(fees.tolist())
        fund_errors.append(abs(expected_funding-trade['funding_fees']))
        ownership[pair] += (at_event > 0).astype(int)
        accrued = np.concatenate([[0.],np.cumsum(fees)])[
            np.searchsorted(event_times, times, side='left')]
        prices = data[pair]['marks']
        total += (sign*(sold+held*prices-bought) - bought*trade['fee_open']
                  - sold*trade['fee_close'] - held*prices*trade['fee_close'] + accrued)
        counts += (held > 0).astype(int)
        gross += held*prices
        signed += sign*held*prices
        if pair == 'TIA/USDT:USDT':
            gap_qty = np.concatenate([[0.],quantities])[np.searchsorted(filled, gap, side='left')]
            if gap_qty > 1e-10:
                unknown_positions.append(dict(pair=pair, trade_id=trade.get('trade_id'),
                                              side='short' if trade['is_short'] else 'long',
                                              quantity=float(gap_qty), event='2026-06-24T04:00Z',
                                              unknown_funding_rate=None))
    error = float(np.max(np.abs(total-frame.hourly_mark_equity.to_numpy())))
    require(error < 3e-5, f'Actual fill/mark/funding NAV mismatch {error}')
    require(np.array_equal(counts, frame.open_trades.to_numpy()), 'Actual open position counts differ')
    require(max(fund_errors, default=0.) < 1e-7, 'Quantity-aware funding differs from native')
    duplicates = sum(int((v > 1).sum()) for v in ownership.values())
    require(duplicates == 0, 'Funding event assigned to multiple same-pair lifecycles')
    return dict(passed=True, hourly_points=len(times), maximum_nav_error_usdt=error,
                maximum_funding_error_usdt=max(fund_errors, default=0.), filled_orders=order_count,
                multi_order_trades=multi, supplied_events=sum(len(x['times']) for x in data.values()),
                assigned_event_lifecycles=sum(int(v.sum()) for v in ownership.values()),
                dual_owned_events=duplicates,
                maximum_gross_mark_exposure_to_nav=float(np.max(gross/frame.hourly_mark_equity)),
                maximum_abs_net_mark_exposure_to_nav=float(np.max(np.abs(signed/frame.hourly_mark_equity))),
                tia_missing_event_actual_exposure=unknown_positions,
                actual_exchange_settlement_certified=False,
                known_missing_tia_event_unexposed=not bool(unknown_positions),
                complete_economic_account_admitted=False)


def policy(plan, candidate, path, stats):
    weights = pd.read_parquet(path/'native_weights.parquet')
    expected = pd.read_parquet(Path(plan['output_root'])/f'preflight_weights_{candidate["strategy"]}.parquet')
    pd.testing.assert_frame_equal(weights, expected, check_exact=True)
    targets = json.loads((path/'native_targets.json').read_text())
    expected_targets = {d.strftime('%Y-%m-%d'):{p:int(np.sign(weights.at[d,p])) for p in weights.columns} for d in weights.index}
    require(targets == expected_targets, 'Direction targets differ from native weights')
    features = pd.read_parquet(path/'native_features.parquet')
    hourly_rows = 0
    for pair in plan['pairs']:
        view = pd.read_parquet(path/f'native_indicators_{pair.split("/")[0]}.parquet')
        view = view.loc[view.date.ge(pd.Timestamp(plan['start']))]
        daily = features.loc[features.pair.eq(pair)].set_index('decision_date')
        for field in ['target_weight','source_signal','source_volatility']:
            ref = daily[field].reindex(pd.DatetimeIndex(view.date.dt.floor('D'))).to_numpy()
            require(np.array_equal(ref, view[field].to_numpy(), equal_nan=True), 'Native hourly feature mapping')
        hourly_rows += len(view)
    with gzip.open(path/'strategy_trace.json.gz', 'rt') as stream:
        trace = json.load(stream)
    nav = pd.read_parquet(path/'hourly_equity.parquet').set_index('date').hourly_mark_equity
    markets = json.loads(Path(plan['market_snapshot']['path']).read_text())['markets']
    execution = {p:pd.read_feather(Path(plan['datadir'])/'futures'/
                   (p.split('/')[0]+'_USDT_USDT-1h-futures.feather')).set_index('date').open
                 for p in plan['pairs']}
    budgets = {}
    maximum_budget_error = 0.
    targets_checked = 0
    for record in trace['budgets']:
        stamp = pd.Timestamp(record['clock'])
        require(stamp not in budgets and stamp.hour == 1, 'Duplicate/off-clock allocation budget')
        equity = record['native_marked_equity']
        maximum_budget_error = max(maximum_budget_error, abs(equity-nav.loc[stamp]))
        for pair in plan['pairs']:
            market = markets[pair]
            contract = market.get('contractSize') or 1.
            tick = market['precision']['amount']*contract
            rate = float(execution[pair].loc[stamp])
            weight = float(weights.at[stamp.floor('D'),pair])
            raw = .99*equity*abs(weight)/rate
            amount = float((Decimal(str(raw))/Decimal(str(tick))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(tick)))
            limits = market['limits']
            # Native cost limit reserves stoploss up to1.5, while the base-amount
            # limit uses only the configured1.05 margin reserve. Do not conflate.
            required_notional = max((limits.get('cost',{}).get('min') or 0.)*contract*1.5,
                                    (limits.get('amount',{}).get('min') or 0.)*contract*rate*1.05)
            if amount*rate < required_notional:
                amount = 0.
            require(abs(amount-record['target_quantities'][pair]) < 1e-8,
                    'Independent quantity precision/minimum budget mismatch')
            targets_checked += 1
        budgets[stamp] = record
    require(maximum_budget_error < 1e-6, 'Sizing budget differs from actual pre-order marked NAV')
    require(len(trace['accepted']) == len(stats['trades']), 'Confirmed initial-entry/native lifecycle count')
    filled_adjustments = 0
    target_overruns = 0
    for trade in stats['trades']:
        opened = pd.Timestamp(trade['open_date'])
        require(opened.hour == 1 and opened.minute == opened.second == 0, 'Late initial entry')
        direction = -1 if trade['is_short'] else 1
        require(targets[opened.strftime('%Y-%m-%d')][trade['pair']] == direction, 'Initial direction target')
        quantity = 0.
        for order in trade['orders']:
            tag = order.get('ft_order_tag')
            if order['ft_is_entry'] or tag == 'source_daily_quantity':
                clock = pd.Timestamp(order['order_filled_timestamp'], unit='ms', tz='UTC')
                require(clock.hour == 1 and clock.minute == clock.second == 0, 'Late quantity adjustment')
            if order['ft_is_entry']:
                quantity += order['amount']
                target = budgets[clock]['target_quantities'][trade['pair']]
                target_overruns += quantity > target+1e-8
            else:
                quantity -= order['amount']
            filled_adjustments += tag == 'source_daily_quantity'
    require(target_overruns == 0, 'Native min-stake upsizing or entry overshoots target quantity')
    require(len(trace['budgets']) == len(weights), 'Missing daily native marked budget')
    require(filled_adjustments <= len(trace['adjustments']), 'More native adjustments than requests')
    return dict(passed=True, exact_native_weight_cells=weights.size,
                native_hourly_feature_rows=hourly_rows, initial_entries=len(trace['accepted']),
                requested_adjustments=len(trace['adjustments']), filled_adjustments=filled_adjustments,
                daily_budget_snapshots=len(trace['budgets']),
                independently_checked_target_quantities=targets_checked,
                maximum_marked_budget_error_usdt=maximum_budget_error,
                native_entry_target_overruns=target_overruns,
                exit_reasons=dict(Counter(t['exit_reason'] for t in stats['trades'])))


def check_account(plan, candidate, path, stats, archive, frame, data):
    grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h').as_unit('ns')
    require(pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid), 'Full account observation grid')
    files = [archive, path/'result.json', path/'hourly_equity.parquet', path/'native_weights.parquet',
             path/'native_features.parquet',path/'native_targets.json',path/'strategy_trace.json.gz']
    files += sorted(path.glob('native_indicators_*.parquet'))
    files += [Path(plan['output_root'])/f'preflight_weights_{candidate["strategy"]}.parquet']
    hashes = {str(p):sha(p) for p in files}
    record = path/'independent_native_audit.json'
    current = sha(__file__)
    if record.exists():
        cached = json.loads(record.read_text())
        if cached.get('auditor_sha256') == current and cached.get('artifact_hashes') == hashes:
            return cached['cash'],cached['marked'],cached['policy']
    cash = audit_trades(stats)
    marked = ledger(stats, frame, candidate['capital'], data)
    rules = policy(plan, candidate, path, stats)
    save(record,dict(cash=cash,marked=marked,policy=rules,auditor_sha256=current,artifact_hashes=hashes))
    return cash,marked,rules


def main():
    report = HERE/'v002'
    plan = json.loads((report/'plan.json').read_text())
    require(json.loads((report/'execution_complete.json').read_text())['accounts'] == 12, 'Incomplete account matrix')
    require(json.loads((report/'full_native_clock_parity.json').read_text())['passed'], 'Clock gate financial parity')
    grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h').as_unit('ns')
    data = inputs(plan, grid)
    output = Path(plan['output_root'])
    navs, rows, audits = {}, [], []
    for candidate in plan['candidates']:
        verify(plan, candidate)
        for cost in plan['total_cost_bps_per_side']:
            key = f'{candidate["name"]}_{cost}bps'
            path = output/key
            receipt = json.loads((path/'process_receipt.json').read_text())
            require(receipt['returncode'] == 0 and sha(path/'result.json') == receipt['result_sha256'], 'Native completion/hash')
            stats, archive, cfg = read_native(path, candidate['strategy'])
            require(cfg['fee'] == cost/10000 and cfg['dry_run_wallet'] == candidate['capital'], 'Archive cost/capital')
            require(cfg['margin_mode'] == 'cross' and cfg['position_adjustment_enable'], 'Archive quantity/margin contract')
            frame = pd.read_parquet(path/'hourly_equity.parquet')
            nav = frame.set_index('date').hourly_mark_equity.loc[plan['start']:plan['comparison_end']]
            require(nav.index.as_unit('ns').equals(grid) and nav.iloc[0] == candidate['capital'], 'Full account clock/capital')
            cash,marked,rules = check_account(plan,candidate,path,stats,archive,frame,data)
            periods = []
            for period in plan['periods']:
                section = nav.loc[period['start']:period['end']]
                periods.append(dict(name=period['name'], **metrics(section)))
            navs[key] = nav
            rows.append(dict(account=candidate['name'], cost_bps_per_side=cost, periods=periods,
                             native_terminal_return_separate=stats['profit_total'],
                             native_trades=len(stats['trades']), filled_orders=marked['filled_orders'],
                             actual_settlement='UNKNOWN' if marked['tia_missing_event_actual_exposure'] else 'UNAUTHENTICATED',
                             tia_missing_event_actual_exposure=marked['tia_missing_event_actual_exposure'],
                             numeric_gate=all(p['net_return'] > 0 and p['maximum_hourly_mark_drawdown'] <= .5 for p in periods)))
            audits.append(dict(account=key, archive=str(archive), archive_sha256=sha(archive),
                               marked=marked, policy=rules))
            print('AUDITED', key, periods[0]['net_return'], 'NAV error', marked['maximum_nav_error_usdt'], flush=True)
    combos, gates = [], []
    for cost in plan['total_cost_bps_per_side']:
        blended = combine(navs[f'CovarianceRiskTS_500_{cost}bps'], navs[f'GrossOneRiskTS_500_{cost}bps'])
        periods = [dict(name=p['name'], **metrics(blended.loc[p['start']:p['end']])) for p in plan['periods']]
        blended.to_frame('hourly_mark_equity').to_parquet(report/f'combo_{cost}bps.parquet')
        standalone = {r['account']:r for r in rows if r['cost_bps_per_side'] == cost and r['account'].endswith('_1000')}
        cov = standalone['CovarianceRiskTS_1000']['periods'][0]['net_return']
        control = max(standalone[s]['periods'][0]['net_return'] for s in ['DiagonalRiskTS_1000','GrossOneRiskTS_1000'])
        best = max(r['periods'][0]['net_return'] for r in standalone.values())
        combo_gate = all(p['net_return'] > 0 and p['maximum_hourly_mark_drawdown'] <= .5 for p in periods) and periods[0]['net_return'] > best
        gates.append(dict(cost=cost, covariance_beats_controls=cov > control,
                          covariance_positive_full_and_years=standalone['CovarianceRiskTS_1000']['numeric_gate'],
                          combo_beats_best_standalone=periods[0]['net_return'] > best,
                          combo_positive_full_and_years=all(p['net_return'] > 0 for p in periods),
                          combined_numeric_gate=combo_gate and cov > control and standalone['CovarianceRiskTS_1000']['numeric_gate']))
        combos.append(dict(cost_bps_per_side=cost, actual_sleeves=['CovarianceRiskTS_500','GrossOneRiskTS_500'],
                           periods=periods, numeric_gate=combo_gate,
                           known_gap_exposed_sleeves=[r['account'] for r in rows
                              if r['cost_bps_per_side']==cost and r['account'] in
                              ['CovarianceRiskTS_500','GrossOneRiskTS_500'] and r['tia_missing_event_actual_exposure']],
                           actual_exchange_settlement_certified=False))
    save(report/'native_validation.json', dict(passed=True, accounts=12, audits=audits,
        actual_native_cash_and_mark_reconciled=True, private_funding_settlement_certified=False))
    save(report/'results.json', dict(accounts=rows, combinations=combos, gates=gates,
         complete_supplied_event_models=12, actual_exchange_settlement_certified=False,
         independent_oos=False, source_portfolio_reproduced=False,
         accepted_deployable_strategy=False, objective_complete=False))


if __name__ == '__main__':
    main()
