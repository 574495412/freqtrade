"""Actual fill/NAV/slot-budget/calendar audit;no candidate calculator imports."""
import calendar
import gzip
import hashlib
import importlib.util
import json
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

import pandas as pd

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def read(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    Path(p).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def active(day, role):
    if role == 'hold':
        return True
    last, first = (3, 2) if role == 'cal32' else (2, 3)
    return day.day <= first or day.day > calendar.monthrange(day.year, day.month)[1] - last


def execution_audit(candidate, plan, nav, audit):
    role = candidate['name'].split('_')[0]
    directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
    with gzip.open(directory / 'strategy_trace.json.gz', 'rt') as f:
        trace = json.load(f)
    stats, _ = audit.native_stats(directory, candidate['strategy'])
    expected = []
    for stamp in pd.date_range(plan['start'], plan['comparison_end'], freq='D'):
        if active(stamp.date(), role) and (stamp == pd.Timestamp(plan['start']) or
                not active((stamp - pd.Timedelta(days=1)).date(), role)):
            expected.append(stamp)
    actual = {}
    natural, force = 0, 0
    for trade in stats['trades']:
        assert len(trade['orders']) == 2
        entry, exit_order = trade['orders']
        assert entry['ft_is_entry'] and not exit_order['ft_is_entry']
        stamp = pd.to_datetime(entry['order_filled_timestamp'], unit='ms', utc=True)
        assert stamp.hour == stamp.minute == stamp.second == 0 and stamp in expected
        key = (trade['pair'], stamp)
        assert key not in actual
        actual[key] = entry
        close = pd.to_datetime(exit_order['order_filled_timestamp'], unit='ms', utc=True)
        if trade['exit_reason'] == 'force_exit':
            assert close >= pd.Timestamp(plan['comparison_end'])
            force += 1
        else:
            assert trade['exit_reason'] == 'source_calendar_window_end'
            assert close.hour == close.minute == close.second == 0
            assert not active(close.date(), role) and active((close - pd.Timedelta(days=1)).date(), role)
            natural += 1
    assert set(actual) == {(pair, stamp) for pair in candidate['pairs'] for stamp in expected}
    assert len(trace['budgets']) == len(trace['confirmed']) == len(actual)
    market = read(candidate['market_snapshot']['path'])['markets']
    maximum = 0.
    for row in trace['budgets']:
        stamp = pd.Timestamp(row['clock'])
        entry = actual[(row['pair'], stamp)]
        assert row['admitted']
        # Native unlimited stake = (available+tied)/maxslots. All entries
        # restart a new two-coin calendar campaign from fully closed cash.
        wanted = .99 * nav.loc[stamp] / 2
        maximum = max(maximum, abs(row['proposed_stake'] - wanted))
        assert maximum < 1e-6
        assert abs(row['requested_stake'] - min(row['proposed_stake'], row['maximum'])) < 1e-8
        unit = D(str(market[row['pair']]['precision']['amount']))
        quantity = (D(str(row['requested_stake'])) / D(str(entry['safe_price'])) / unit).to_integral_value(rounding=ROUND_DOWN) * unit
        assert abs(quantity - D(str(entry['amount']))) < D('1e-10')
        assert entry['amount'] * entry['safe_price'] <= row['requested_stake'] + 1e-7
    return dict(account=candidate['name'], expected_calendar_campaign_starts=len(expected),
                actual_entry_budgets=len(actual), checked_entry_quantities=len(actual),
                maximum_native_slot_budget_error_usdt=float(maximum), naturally_closed=natural,
                separate_terminal_force_exits=force, actual_clock_and_quantity_exact=True, passed=True)


def endpoint_components(candidate, plan, nav, audit):
    directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
    stats, _ = audit.native_stats(directory, candidate['strategy'])
    edge = int(pd.Timestamp(plan['comparison_end']).timestamp() * 1000)
    pnl = fees = reserve = rounding = D(0)
    count = excluded = 0
    closed_errors = []
    for trade in stats['trades']:
        orders = [o for o in trade['orders'] if o['order_filled_timestamp'] < edge]
        count += len(orders)
        excluded += len(trade['orders']) - len(orders)
        bought = sum((D(str(o['amount'])) * D(str(o['safe_price'])) for o in orders if o['ft_is_entry']), D(0))
        sold = sum((D(str(o['amount'])) * D(str(o['safe_price'])) for o in orders if not o['ft_is_entry']), D(0))
        quantity = sum((D(str(o['amount'])) * (1 if o['ft_is_entry'] else -1) for o in orders), D(0))
        price_file = Path(plan['datadir']) / (trade['pair'].replace('/', '_') + '-1h.feather')
        mark = D(str(pd.read_feather(price_file).set_index('date').loc[pd.Timestamp(plan['comparison_end']), 'open']))
        charge = bought * D(str(trade['fee_open'])) + sold * D(str(trade['fee_close']))
        pnl += sold + quantity * mark - bought
        fees += charge
        reserve += quantity * mark * D(str(trade['fee_close']))
        if trade['close_timestamp'] < edge:
            assert quantity == 0
            # Native closed Trade profit rounds quote values to8decimals.
            # Bind this observed per-trade difference explicitly; the previous
            # UP helper had no naturally closed trades and could omit it.
            difference = D(str(trade['profit_abs'])) - (sold - bought - charge)
            rounding += difference
            closed_errors.append(abs(difference))
    raw = pnl - fees - reserve
    assert abs(D(str(nav.iloc[-1])) - D(str(candidate['capital'])) - raw - rounding) < D('1e-8')
    assert excluded == 2
    return dict(account=candidate['name'], gross_marked_price_pnl_usdt=float(pnl),
                paid_quote_fees_usdt=float(fees), remaining_exit_fee_reserve_usdt=float(reserve),
                unrounded_economic_net_change_usdt=float(raw),
                native_closed_profit_rounding_adjustment_usdt=float(rounding),
                maximum_observed_closed_profit_rounding_usdt=float(max(closed_errors, default=D(0))),
                native_common_endpoint_net_change_usdt=float(raw + rounding),
                common_endpoint_actual_fills=count, actual_force_exit_fills_excluded=excluded)


def main():
    plan = read(HERE / 'plan.json')
    assert read(HERE / 'execution_complete.json')['accounts'] == 12
    for path, digest in read(HERE / 'implementation_freeze.json').items():
        assert sha(path) == digest, path
    audit = load('actual_native_cash_audit', HERE / 'audit_accounts.py')
    oracle_path = ROOT.parent / 'TradingAgents/research_tools/nfi_reproduction/audit_original.py'
    oracle = load('independent_cash_oracle', oracle_path)
    grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h').as_unit('ns')
    navs, accounts, executions, components = {}, [], [], []
    for candidate in plan['candidates']:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        navs[candidate['name']] = nav
        accounts.append(row)
        executions.append(execution_audit(candidate, plan, nav, audit))
        components.append(endpoint_components(candidate, plan, nav, audit))
        print('AUDITED', candidate['name'], row['periods'][0]['net_return'], flush=True)
    for row in read(HERE / 'parent_seals_before.json'):
        assert sha(row['path']) == row['sha256']
        for r in read(row['path'])['files']:
            assert sha(r['path']) == r['sha256'], r['path']
    parent = HERE.with_name('nfi_carry_matched_native_20261115_v1')
    old_plan, old_results = read(parent / 'plan.json'), read(parent / 'results.json')
    for row in old_results['accounts']:
        if row['role'] not in ['nfi_spot', 'nfi_futures', 'carry']:
            continue
        name = row['account']
        if row['role'].startswith('nfi_'):
            c = next(c for c in old_plan['candidates'] if c['name'] == name)
            path = Path(old_plan['output_root']) / (name + f"_{c['cost_bps']}bps") / 'hourly_equity.parquet'
        else:
            path = HERE.with_name('paired_carry_reservation_native_20261115_v2') / (name + '.parquet')
        nav = pd.read_parquet(path).set_index('date').hourly_mark_equity
        assert nav.index.as_unit('ns').equals(grid)
        navs[name] = nav
        accounts.append(dict(account=name, role=row['role'], capital=row['capital'], scenario=row['scenario'],
                             periods=audit.periods(nav, plan), reused_sealed_parent=True, new_native_runs=0))
    gates = []
    for row in accounts:
        if row['role'] not in ['cal32', 'cal23']:
            continue
        p = row['periods']
        controls = [r for r in accounts if r['capital'] == row['capital'] and r['scenario'] == row['scenario']
                    and r['role'] in ['hold', 'nfi_spot']]
        assert len(controls) == 2
        positive = all(x['net_return'] > 0 for x in p)
        beats = all(p[0]['net_return'] > c['periods'][0]['net_return'] for c in controls)
        numeric = positive and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
        gates.append(dict(account=row['account'], positive_all_periods=positive, beats_matched_controls=beats,
                          numeric_gate=numeric, frozen_financial_gate=numeric and beats))
    portfolios = []
    for scenario in ['conservative', 'lower']:
        for a, b in [('cal32', 'cal23'), ('cal32', 'nfi_spot'), ('cal23', 'nfi_spot'),
                     ('cal32', 'carry'), ('cal23', 'carry')]:
            sleeves = [a + '_500_' + scenario, b + '_500_' + scenario]
            nav = sum(navs[s] for s in sleeves)
            assert nav.iloc[0] == 1000
            p = audit.periods(nav, plan)
            controls = [r for r in accounts if r['capital'] == 1000 and r['scenario'] == scenario
                        and r['role'] in ['cal32', 'cal23', 'hold', 'nfi_spot']]
            best = max(controls, key=lambda r: r['periods'][0]['net_return'])
            numeric = all(x['net_return'] > 0 for x in p) and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
            beats = p[0]['net_return'] > best['periods'][0]['net_return']
            dd = p[0]['hourly_mark_drawdown'] <= best['periods'][0]['hourly_mark_drawdown']
            name = a + '_' + b + '_1000_' + scenario
            pd.DataFrame(dict(date=nav.index, hourly_mark_equity=nav.to_numpy())).to_parquet(HERE / (name + '.parquet'), index=False)
            portfolios.append(dict(name=name, sleeves=sleeves, actual500plus500=True, scaled_curves=False,
                                    transfers=False, scenario=scenario, periods=p, best_matched1000=best['account'],
                                    beats_best=beats, no_dd_worsening=dd, numeric_gate=numeric,
                                    frozen_financial_gate=numeric and beats and dd, source_overlap=b == 'cal23',
                                    carry_has_different_market_contract=b == 'carry'))
    save(HERE / 'execution_audit.json', dict(passed=True, accounts=executions,
         actual_entry_budgets=sum(r['actual_entry_budgets'] for r in executions),
         actual_natural_closes=sum(r['naturally_closed'] for r in executions),
         separate_force_exits=sum(r['separate_terminal_force_exits'] for r in executions)))
    save(HERE / 'endpoint_components.json', components)
    result = dict(new_native_wallets=12, new_native_trades=sum(r.get('actual_native_trades', 0) for r in accounts),
                  new_simulated_fills=sum(r.get('filled_orders', 0) for r in accounts), new_hour_points=12*len(grid),
                  maximum_independent_nav_error_usdt=max(r.get('maximum_nav_error_usdt', 0) for r in accounts),
                  accounts=accounts, portfolios=portfolios, standalone_gates=gates,
                  existing_nfi_wallets_reused=8, existing_carry_leg_wallets_reused=24,
                  independent_cash_oracle=dict(path=str(oracle_path), sha256=sha(oracle_path)),
                  endpoint_rounding_reconciled_explicitly=True,
                  new_accepted_independent_alpha=0, independent_oos=False, source_author_account_reproduced=False,
                  orders_enabled=False, services_changed=False)
    save(HERE / 'results.json', result)
    print('COMPLETE', result['new_native_trades'], 'trades', result['new_simulated_fills'], 'fills;',
          [(p['name'], p['periods'][0]['net_return'], p['frozen_financial_gate']) for p in portfolios], flush=True)


if __name__ == '__main__':
    main()
