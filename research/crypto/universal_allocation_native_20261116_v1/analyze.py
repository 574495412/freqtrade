"""Audit actual native accounts/budgets and sum predeclared real500wallets."""
import gzip
import hashlib
import importlib.util
import json
import math
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


def check_budget(candidate, plan, nav, audit):
    directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
    with gzip.open(directory / 'strategy_trace.json.gz', 'rt') as stream:
        trace = json.load(stream)
    weights = pd.DataFrame(trace['weights']).set_index('day')
    weights.index = pd.to_datetime(weights.index, utc=True)
    mode = candidate['name'].split('_')[0]
    pre = 'preflight_up_1000_conservative_admission2' if mode == 'up' else 'preflight_crp_1000_conservative_admission1'
    reference = pd.read_parquet(Path(plan['output_root']) / pre / 'native_weights.parquet')
    weights.index.name = reference.index.name
    pd.testing.assert_frame_equal(weights, reference, check_freq=False)
    assert len(trace['budgets']) == 608
    market = read(candidate['market_snapshot']['path'])['markets']
    maximum = 0.
    for row in trace['budgets']:
        stamp = pd.Timestamp(row['clock'])
        assert stamp.hour == 1 and stamp.minute == 0
        maximum = max(maximum, abs(row['native_marked_equity'] - nav.loc[stamp]))
        assert maximum < 1e-6
        for pair, target in row['target_quantities'].items():
            unit = D(str(market[pair]['precision']['amount']))
            raw = D('.99') * D(str(row['native_marked_equity'])) * D(str(weights.at[stamp.floor('D'), pair])) / D(str(row['prices'][pair]))
            expected = (raw / unit).to_integral_value(rounding=ROUND_DOWN) * unit
            assert abs(D(str(target)) - expected) < D('1e-10'), (candidate['name'], stamp, pair)
            # This entire cohort stays above the minimum: no minimum-sized
            # target was silently converted into a different capital contract.
            assert float(expected) * row['prices'][pair] > 10
    stats, _ = audit.native_stats(directory, candidate['strategy'])
    orders = [o for t in stats['trades'] for o in t['orders']]
    for trade in stats['trades']:
        assert trade['exit_reason'] == 'force_exit'
        for order in trade['orders'][:-1]:
            stamp = pd.to_datetime(order['order_filled_timestamp'], unit='ms', utc=True)
            assert stamp.hour == 1 and stamp.minute == 0
    rejected = sum(not row['accepted'] for row in trace['accepted'])
    return dict(account=candidate['name'], passed=True, actual_budget_days=608,
                independent_target_quantities=1216, maximum_budget_nav_error_usdt=maximum,
                nonterminal_fills_at_registered_daily_clock=True, filled_orders=len(orders),
                actual_source_weights_exact_across_capital_and_cost=True, rejected_confirmations=rejected,
                native_adjustment_intents=len(trace['adjustments']))


def endpoint_components(candidate, plan, nav, audit):
    directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
    stats, _ = audit.native_stats(directory, candidate['strategy'])
    edge = int(pd.Timestamp(plan['comparison_end']).timestamp() * 1000)
    price_pnl, fees, reserve, count, excluded = [], [], [], 0, 0
    for trade in stats['trades']:
        orders = [o for o in trade['orders'] if o['order_filled_timestamp'] < edge]
        count += len(orders)
        excluded += len(trade['orders']) - len(orders)
        bought = math.fsum(o['amount'] * o['safe_price'] for o in orders if o['ft_is_entry'])
        sold = math.fsum(o['amount'] * o['safe_price'] for o in orders if not o['ft_is_entry'])
        quantity = math.fsum(o['amount'] * (1 if o['ft_is_entry'] else -1) for o in orders)
        path = Path(plan['datadir']) / (trade['pair'].replace('/', '_') + '-1h.feather')
        mark = float(pd.read_feather(path).set_index('date').loc[pd.Timestamp(plan['comparison_end']), 'open'])
        price_pnl.append(sold + quantity * mark - bought)
        fees.append(bought * trade['fee_open'] + sold * trade['fee_close'])
        reserve.append(quantity * mark * trade['fee_close'])
    change = math.fsum(price_pnl) - math.fsum(fees) - math.fsum(reserve)
    assert abs(change - (nav.iloc[-1] - candidate['capital'])) < 1e-8
    assert excluded == 2
    return dict(account=candidate['name'], gross_marked_price_pnl_usdt=math.fsum(price_pnl),
                paid_quote_fees_usdt=math.fsum(fees), remaining_exit_fee_reserve_usdt=math.fsum(reserve),
                common_endpoint_net_change_usdt=change, common_endpoint_actual_fills=count,
                actual_force_exit_fills_excluded=excluded)


def main():
    plan = read(HERE / 'plan.json')
    assert read(HERE / 'execution_complete.json')['accounts'] == 8
    for path, digest in read(HERE / 'implementation_freeze.json').items():
        assert sha(path) == digest, path
    audit = load('independent_native_fill_audit', HERE / 'audit_accounts.py')
    oracle_path = ROOT.parent / 'TradingAgents/research_tools/nfi_reproduction/audit_original.py'
    oracle = load('independent_cash_oracle', oracle_path)
    grid = pd.date_range(plan['start'], plan['comparison_end'], freq='h').as_unit('ns')
    navs, accounts, budgets, components = {}, [], [], []
    for candidate in plan['candidates']:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        navs[candidate['name']] = nav
        accounts.append(row)
        budgets.append(check_budget(candidate, plan, nav, audit))
        components.append(endpoint_components(candidate, plan, nav, audit))
        print('AUDITED', candidate['name'], row['periods'][0]['net_return'], flush=True)
    parent = HERE.with_name('nfi_carry_matched_native_20261115_v1')
    parent_plan = read(parent / 'plan.json')
    parent_results = read(parent / 'results.json')
    for row in read(HERE / 'parent_seals_before.json'):
        assert sha(row['path']) == row['sha256']
        for file in read(row['path'])['files']:
            assert sha(file['path']) == file['sha256']
    for row in parent_results['accounts']:
        name = row['account']
        if row['role'].startswith('nfi_'):
            c = next(c for c in parent_plan['candidates'] if c['name'] == name)
            path = Path(parent_plan['output_root']) / (name + f"_{c['cost_bps']}bps") / 'hourly_equity.parquet'
        else:
            path = HERE.with_name('paired_carry_reservation_native_20261115_v2') / (name + '.parquet')
        frame = pd.read_parquet(path)
        nav = frame.set_index('date').hourly_mark_equity
        assert nav.index.as_unit('ns').equals(grid)
        navs[name] = nav
        accounts.append(dict(account=name, role=row['role'], capital=row['capital'], scenario=row['scenario'],
                             periods=audit.periods(nav, plan), reused_sealed_parent=True, new_native_runs=0))
    gates, portfolios = [], []
    for row in accounts:
        if row['role'] != 'up':
            continue
        capital, scenario, p = row['capital'], row['scenario'], row['periods']
        controls = [r for r in accounts if r['capital'] == capital and r['scenario'] == scenario
                    and r['role'] in ['crp', 'benchmark', 'nfi_spot']]
        assert len(controls) == 3
        positive = all(x['net_return'] > 0 for x in p)
        beats = all(p[0]['net_return'] > c['periods'][0]['net_return'] for c in controls)
        numeric = positive and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
        gates.append(dict(account=row['account'], positive_all_periods=positive, beats_spot_controls=beats,
                          numeric_gate=numeric, frozen_financial_gate=numeric and beats))
    for scenario in ['conservative', 'lower']:
        for other in ['crp', 'nfi_spot', 'carry']:
            sleeves = ['up_500_' + scenario, other + '_500_' + scenario]
            nav = sum(navs[n] for n in sleeves)
            assert nav.iloc[0] == 1000
            p = audit.periods(nav, plan)
            controls = [r for r in accounts if r['capital'] == 1000 and r['scenario'] == scenario]
            best = max(controls, key=lambda r: r['periods'][0]['net_return'])
            spot_controls = [r for r in controls if r['role'] not in ['carry', 'nfi_futures']]
            best_spot = max(spot_controls, key=lambda r: r['periods'][0]['net_return'])
            numeric = all(x['net_return'] > 0 for x in p) and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
            beats = p[0]['net_return'] > best['periods'][0]['net_return']
            dd = p[0]['hourly_mark_drawdown'] <= best['periods'][0]['hourly_mark_drawdown']
            name = 'up_' + other + '_1000_' + scenario
            pd.DataFrame(dict(date=nav.index, hourly_mark_equity=nav.to_numpy())).to_parquet(HERE / (name + '.parquet'), index=False)
            portfolios.append(dict(name=name, sleeves=sleeves, actual500plus500=True, scaled_curves=False,
                                    transfers=False, scenario=scenario, periods=p,
                                    best_same_pool_capital_control=best['account'],
                                    best_spot_control=best_spot['account'],
                                    beats_best_spot=p[0]['net_return'] > best_spot['periods'][0]['net_return'],
                                    beats_best_capital_control=beats, no_dd_worsening=dd,
                                    numeric_gate=numeric, frozen_financial_gate=numeric and beats and dd,
                                    source_overlap=other == 'crp'))
    save(HERE / 'budget_audit.json', dict(passed=True, accounts=budgets,
         complete_daily_budgets=8*608, independently_checked_quantities=8*1216,
         source_account_calculator_imported=False))
    save(HERE / 'endpoint_components.json', components)
    result = dict(new_native_wallets=8, new_native_trades=sum(r.get('actual_native_trades', 0) for r in accounts),
                  new_simulated_fills=sum(r.get('filled_orders', 0) for r in accounts),
                  new_hour_points=8*len(grid), maximum_independent_nav_error_usdt=max(r.get('maximum_nav_error_usdt', 0) for r in accounts),
                  accounts=accounts, portfolios=portfolios, standalone_gates=gates,
                  existing_native_nfi_wallets_reused=8, existing_carry_leg_wallets_reused=24,
                  independent_cash_oracle=dict(path=str(oracle_path), sha256=sha(oracle_path)),
                  new_accepted_independent_alpha=0, independent_oos=False,
                  source_author_account_reproduced=False, orders_enabled=False, services_changed=False)
    save(HERE / 'results.json', result)
    print('COMPLETE', result['new_native_trades'], 'trades', result['new_simulated_fills'], 'fills;',
          [(p['name'], p['periods'][0]['net_return'], p['frozen_financial_gate']) for p in portfolios], flush=True)


if __name__ == '__main__':
    main()
