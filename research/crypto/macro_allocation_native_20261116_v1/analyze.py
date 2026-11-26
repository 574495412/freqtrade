"""Actual fill/NAV/slot-budget/calendar audit;no candidate calculator imports."""
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


def execution_audit(candidate, plan, nav, audit):
    directory=Path(plan['output_root'])/(candidate['name']+f"_{candidate['cost_bps']}bps")
    with gzip.open(directory/'strategy_trace.json.gz','rt') as f:trace=json.load(f)
    role=candidate['name'].split('_')[0]
    reference=read(Path(plan['output_root'])/('preflight_'+role+'_1000_conservative')/'strategy_policy.json')
    assert trace['policy']==reference
    policy={pd.Timestamp(r['clock']):r['fraction'] for r in reference}
    assert len(trace['budgets'])==len(policy)==(1 if role=='hold' else 609)
    market=read(candidate['market_snapshot']['path'])['markets']
    maximum=0.;checks=0
    for row in trace['budgets']:
        stamp=pd.Timestamp(row['clock']);assert stamp in policy
        maximum=max(maximum,abs(row['native_marked_equity']-nav.loc[stamp]))
        assert maximum<1e-6
        for pair,target in row['target_quantities'].items():
            fraction=policy[stamp] if pair=='BTC/USDT' else 0.
            unit=D(str(market[pair]['precision']['amount']))
            raw=D('.99')*D(str(nav.loc[stamp]))*D(str(fraction))/D(str(row['prices'][pair]))
            expected=(raw/unit).to_integral_value(rounding=ROUND_DOWN)*unit
            assert abs(D(str(target))-expected)<D('1e-10'),(candidate['name'],stamp,pair,target,str(expected))
            if pair=='BTC/USDT':assert float(expected)*row['prices'][pair]>10
            checks+=1
    stats,_=audit.native_stats(directory,candidate['strategy'])
    assert len(stats['trades'])==1 and stats['trades'][0]['pair']=='BTC/USDT'
    trade=stats['trades'][0];assert trade['exit_reason']=='force_exit'
    assert not trade['is_short'] and trade['leverage']==1
    for order in trade['orders'][:-1]:
        stamp=pd.to_datetime(order['order_filled_timestamp'],unit='ms',utc=True)
        assert stamp in policy
    assert all(row['accepted'] for row in trace['accepted'])
    assert len({(r['pair'],r['clock']) for r in trace['adjustments']})==len(trace['adjustments'])
    return dict(account=candidate['name'],passed=True,actual_budget_clocks=len(policy),
        independent_target_quantities=checks,maximum_budget_nav_error_usdt=maximum,
        actual_natural_closes=0,separate_terminal_force_exits=1,
        filled_orders=len(trade['orders']),native_adjustment_intents=len(trace['adjustments']),
        policy_exact_across_capital_and_cost=True,actual_fill_clock_admitted=True)


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
    assert excluded == sum(t['exit_reason'] == 'force_exit' for t in stats['trades'])
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
        if row['role'] not in ['nfi_spot', 'carry']:
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
        if row['role'] not in ['macro']:
            continue
        p = row['periods']
        controls = [r for r in accounts if r['capital'] == row['capital'] and r['scenario'] == row['scenario']
                    and r['role'] in ['full','hold', 'nfi_spot']]
        assert len(controls) == 3
        positive = all(x['net_return'] > 0 for x in p)
        beats = all(p[0]['net_return'] > c['periods'][0]['net_return'] for c in controls)
        numeric = positive and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
        gates.append(dict(account=row['account'], positive_all_periods=positive, beats_matched_controls=beats,
                          numeric_gate=numeric, frozen_financial_gate=numeric and beats))
    portfolios = []
    for scenario in ['conservative', 'lower']:
        for a, b in [('macro','nfi_spot'),('macro','carry'),('macro','hold')]:
            sleeves = [a + '_500_' + scenario, b + '_500_' + scenario]
            nav = sum(navs[s] for s in sleeves)
            assert nav.iloc[0] == 1000
            p = audit.periods(nav, plan)
            controls = [r for r in accounts if r['capital'] == 1000 and r['scenario'] == scenario
                        and r['role'] in ['macro','full','hold','nfi_spot']]
            best = max(controls, key=lambda r: r['periods'][0]['net_return'])
            numeric = all(x['net_return'] > 0 for x in p) and p[0]['hourly_mark_drawdown'] <= .3 and p[0]['daily_nav_sharpe'] is not None and p[0]['daily_nav_sharpe'] > .5
            beats = p[0]['net_return'] > best['periods'][0]['net_return']
            dd = p[0]['hourly_mark_drawdown'] <= best['periods'][0]['hourly_mark_drawdown']
            name = a + '_' + b + '_1000_' + scenario
            pd.DataFrame(dict(date=nav.index, hourly_mark_equity=nav.to_numpy())).to_parquet(HERE / (name + '.parquet'), index=False)
            portfolios.append(dict(name=name, sleeves=sleeves, actual500plus500=True, scaled_curves=False,
                                    transfers=False, scenario=scenario, periods=p, best_matched1000=best['account'],
                                    beats_best=beats, no_dd_worsening=dd, numeric_gate=numeric,
                                    frozen_financial_gate=numeric and beats and dd, source_overlap=b == 'hold',
                                    carry_has_different_market_contract=b == 'carry'))
    save(HERE/'execution_audit.json',dict(passed=True,accounts=executions,actual_budget_clocks=sum(r['actual_budget_clocks'] for r in executions),independent_target_quantities=sum(r['independent_target_quantities'] for r in executions),actual_natural_closes=0,separate_force_exits=12))
    save(HERE / 'endpoint_components.json', components)
    result = dict(new_native_wallets=12, new_native_trades=sum(r.get('actual_native_trades', 0) for r in accounts),
                  new_simulated_fills=sum(r.get('filled_orders', 0) for r in accounts), new_hour_points=12*len(grid),
                  maximum_independent_nav_error_usdt=max(r.get('maximum_nav_error_usdt', 0) for r in accounts),
                  accounts=accounts, portfolios=portfolios, standalone_gates=gates,
                  existing_nfi_wallets_reused=4, existing_hold_wallets_reused=0, existing_carry_leg_wallets_reused=24,
                  independent_cash_oracle=dict(path=str(oracle_path), sha256=sha(oracle_path)),
                  endpoint_rounding_reconciled_explicitly=True,
                  new_accepted_independent_alpha=0, historical_macro_firstseen_certified=False, macro_accounts_model_only=True, independent_oos=False, source_author_account_reproduced=False,
                  orders_enabled=False, services_changed=False)
    save(HERE / 'results.json', result)
    print('COMPLETE', result['new_native_trades'], 'trades', result['new_simulated_fills'], 'fills;',
          [(p['name'], p['periods'][0]['net_return'], p['frozen_financial_gate']) for p in portfolios], flush=True)


if __name__ == '__main__':
    main()
