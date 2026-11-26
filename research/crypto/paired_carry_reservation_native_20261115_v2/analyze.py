"""Independent actual-order NAV, funding and multi-wallet reconstruction."""
import gzip
import hashlib
import json
import math
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,default=str,allow_nan=False)+'\n')


def metrics(nav):
    values=nav.to_numpy();assert np.isfinite(values).all()and(values>0).all()
    daily=nav.reindex(pd.date_range(nav.index[0],nav.index[-1],freq='24h')).pct_change(fill_method=None).dropna()
    sd=daily.std(ddof=1);years=(nav.index[-1]-nav.index[0]).total_seconds()/(365*86400)
    return dict(net_return=float(values[-1]/values[0]-1),initial_usdt=float(values[0]),ending_usdt=float(values[-1]),
        cagr_365day=float((values[-1]/values[0])**(1/years)-1),
        hourly_mark_drawdown=float((1-values/np.maximum.accumulate(values)).max()),
        daily_nav_sharpe=float(daily.mean()/sd*math.sqrt(365))if sd>0 else None,
        calendar_hours=len(nav),complete_24h_returns=len(daily))


def periods(nav,plan):return [dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]))for p in plan['periods']]


def audit(candidate,directory,plan,grid):
    assert read(directory/'process_receipt.json')['returncode']==0
    assert read(directory/'process_receipt.json')['result_sha256']==sha(directory/'result.json')
    archives=list((directory/'native_results').glob('*.zip'));assert len(archives)==1
    with ZipFile(archives[0])as archive:
        name=next(n for n in archive.namelist()if n.endswith('.json')and not n.endswith('_config.json'))
        stats=json.loads(archive.read(name))['strategy'][candidate['strategy']]
    assert stats['dry_run_wallet']==candidate['capital']and stats['max_open_trades']==1
    config=read(directory/'config.json');assert config['fee']==candidate['cost_bps']/10000
    assert config['trading_mode']==candidate['mode']and config['research_quantity']==candidate['quantity']
    assert len(stats['trades'])==1
    trade=stats['trades'][0];orders=trade['orders'];assert len(orders)==2
    entry,exit=orders;assert entry['ft_is_entry']and not exit['ft_is_entry']
    quantity=D(str(entry['amount']));assert quantity==D(str(exit['amount']))==D(str(candidate['quantity']))
    assert trade['leverage']==1 and trade['is_short']==(candidate['mode']=='futures')
    begin,end=entry['order_filled_timestamp'],exit['order_filled_timestamp']
    assert begin==int(pd.Timestamp('2025-01-01T00:05Z').timestamp()*1000)
    a=quantity*D(str(entry['safe_price']));b=quantity*D(str(exit['safe_price']))
    side=-1 if trade['is_short']else 1;funding=D(str(trade['funding_fees']or 0))
    pnl=side*(b-a)-a*D(str(trade['fee_open']))-b*D(str(trade['fee_close']))+funding
    assert abs(pnl-D(str(trade['profit_abs'])))<D('.00001')
    assert abs(float(pnl)-stats['profit_total_abs'])<1e-5
    assert abs(candidate['capital']+float(pnl)-stats['final_balance'])<1e-5
    pair=candidate['pairs'][0];mark=pd.read_feather(plan['marks'][pair]['path']).set_index('date').open.reindex(grid)
    assert np.isfinite(mark).all()
    clock=grid.as_unit('ms').asi8
    bought=np.where(clock>begin,float(a),0.);sold=np.where(clock>end,float(b),0.)
    held=float(quantity)*((clock>begin).astype(int)-(clock>end).astype(int))
    accrued=np.zeros(len(clock));events_count=0;fund_error=0.
    if candidate['mode']=='futures':
        stem=pair.replace('/','_').replace(':','_')
        f=pd.read_feather(Path(plan['datadir'])/'futures'/(stem+'-1h-funding_rate.feather'))
        f=f.loc[f.date.ge(pd.Timestamp(plan['start']))&f.date.lt(pd.Timestamp(plan['end']))]
        assert len(f)==1824
        times=pd.DatetimeIndex(f.date).as_unit('ms').asi8
        unit=f.funding_rate.to_numpy()*mark.reindex(pd.DatetimeIndex(f.date)).to_numpy()
        active=(times>=begin)&(times<=end)
        cash=np.where(active,float(quantity)*unit,0.)
        fund_error=abs(math.fsum(cash.tolist())-float(funding));assert fund_error<1e-7
        accrued=np.concatenate([[0.],np.cumsum(cash)])[np.searchsorted(times,clock,side='left')]
        events_count=int(active.sum())
    else:assert funding==0
    reconstructed=candidate['capital']+side*(sold+held*mark.to_numpy()-bought)
    reconstructed-=bought*trade['fee_open']+sold*trade['fee_close']+held*mark.to_numpy()*trade['fee_close']
    reconstructed+=accrued
    frame=pd.read_parquet(directory/'hourly_equity.parquet')
    assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid)
    error=float(np.max(np.abs(reconstructed-frame.hourly_mark_equity.to_numpy())))
    assert error<3e-5,error
    assert np.array_equal(held>0,frame.open_trades.to_numpy()>0)
    market=read(candidate['market_snapshot']['path'])['markets'][pair]
    assert quantity%D(str(market['precision']['amount']))==0
    for order in orders:assert D(str(order['safe_price']))%D(str(market['precision']['price']))==0
    with gzip.open(directory/'strategy_trace.json.gz','rt')as stream:trace=json.load(stream)
    assert len(trace['confirmed'])==len(trace['budgets'])==1
    budget=trace['budgets'][0]
    assert budget['admitted']and float(a)<=budget['budget']+1e-8
    requested=D(str(budget['requested']));rate=D(str(budget['rate']))
    step=D(str(market['precision']['amount']));epsilon=D('1e-10')
    assert requested+epsilon>=D(str(budget['min_stake']))
    assert requested*(1+D(str(config['fee'])))<=D(str(budget['budget']))+epsilon
    # Independent grid interval proves reservation cannot add a quantity lot.
    assert a<=requested+epsilon and requested<(quantity+step)*rate
    assert quantity>=D(str(market['limits']['amount']['min']or 0))
    assert a>=D(str(market['limits']['cost']['min']or 0))
    assert trace['confirmed'][0]['quantity']==candidate['quantity']
    assert read(directory/'native_signal_receipts.json')[candidate['strategy']+'|'+pair]==read(HERE/'preflight_signal_receipts.json')[candidate['name']]
    nav=frame.set_index('date').hourly_mark_equity
    observed=read(directory/'result.json')
    for actual,expected in zip(periods(nav,plan),observed['periods'],strict=True):
        for key in ['net_return','hourly_mark_drawdown','daily_nav_sharpe']:
            assert abs(actual[key]-expected[key])<1e-12
        assert actual['complete_24h_returns']==expected['complete_24h_returns']
    result=dict(passed=True,archive_sha256=sha(archives[0]),auditor_sha256=sha(__file__),
        max_nav_error_usdt=error,funding_error_usdt=fund_error,native_funding_usdt=float(funding),
        source_funding_events_at_actual_position=events_count,filled_orders=2,
        actual_quantity=float(quantity),entry_time=begin,exit_time=end,exit_reason=trade['exit_reason'],
        native_forced_exit_after_common_endpoint=trade['exit_reason']=='force_exit'and end>clock[-1],
        reservation_requested_usdt=float(requested),actual_entry_notional_usdt=float(a),
        native_minimum_usdt=budget['min_stake'],reservation_keeps_exact_quantity=True,
        private_settlement_and_historical_rule_certification=False)
    save(directory/'independent_account_audit.json',result)
    return nav,held,mark.to_numpy(),accrued,trade,result


def main():
    plan=read(HERE/'plan.json');out=Path(plan['output_root'])
    execution=read(HERE/'execution_complete.json')
    assert execution['accounts']==len(plan['candidates'])==24
    assert read(HERE/'pre500_parity.json')['passed']
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');assert len(grid)==14592
    navs={};holdings={};legs=[];details={};maximum=0.
    for c in plan['candidates']:
        name=c['name'];directory=out/(name+f"_{c['cost_bps']}bps")
        nav,held,mark,fund,trade,receipt=audit(c,directory,plan,grid)
        navs[name]=nav;holdings[name]=held;details[name]=(mark,fund,trade,receipt)
        legs.append(dict(name=name,**receipt));maximum=max(maximum,receipt['max_nav_error_usdt'])
    rows=[];portfolios=[];gates=[]
    for scenario in ['conservative','lower']:
        for capital in [1000,500]:
            for role in ['carry','benchmark']:
                names=[c['name']for c in plan['candidates']if c['scenario']==scenario and c['portfolio_capital']==capital and c['role']==role]
                assert len(names)==(4 if role=='carry'else 2)
                if not all(n in navs for n in names):
                    rows.append(dict(role=role,capital=capital,scenario=scenario,periods=None,
                        full_common_window_hedge=False,actual_wallets=sum(n in navs for n in names),
                        state='INCOMPLETE_NATIVE_MINIMUM_REJECTION',return_usdt=None))
                    continue
                nav=sum(navs[n]for n in names);assert nav.iloc[0]==capital
                full_hedge=True;basis=fees=reserve=funding=0.
                if role=='carry':
                    for symbol in ['BTC','ETH']:
                        a=f'carry_{symbol}_spot_{capital}_{scenario}';b=f'carry_{symbol}_futures_{capital}_{scenario}'
                        full_hedge &= np.array_equal(holdings[a],holdings[b])
                        full_hedge &= details[a][3]['native_forced_exit_after_common_endpoint']and details[b][3]['native_forced_exit_after_common_endpoint']
                        for name,sign in [(a,1),(b,-1)]:
                            mark,fund,t,receipt=details[name];q=receipt['actual_quantity'];price=t['orders'][0]['safe_price']
                            basis+=sign*q*(mark[-1]-price)
                            fees+=q*price*t['fee_open'];reserve+=q*mark[-1]*t['fee_close'];funding+=fund[-1]
                    if full_hedge:assert abs(capital+basis-fees-reserve+funding-nav.iloc[-1])<3e-5
                p=periods(nav,plan)
                assert abs((1+p[1]['net_return'])*(1+p[2]['net_return'])-1-p[0]['net_return'])<1e-12
                name=f'{role}_{capital}_{scenario}';navs[name]=nav
                pd.DataFrame(dict(date=nav.index,hourly_mark_equity=nav.to_numpy())).to_parquet(HERE/(name+'.parquet'),index=False)
                rows.append(dict(role=role,capital=capital,scenario=scenario,periods=p,
                    full_common_window_hedge=bool(full_hedge)if role=='carry'else None,
                    actual_wallets=len(names),price_or_basis_pnl_usdt=basis if role=='carry'else None,
                    entry_cost_usdt=fees if role=='carry'else None,remaining_exit_cost_usdt=reserve if role=='carry'else None,
                    native_funding_before_endpoint_usdt=funding if role=='carry'else None))
        if 'carry_500_'+scenario in navs:
            mixed=navs['carry_500_'+scenario]+navs['benchmark_500_'+scenario]
            pd.DataFrame(dict(date=mixed.index,hourly_mark_equity=mixed.to_numpy())).to_parquet(HERE/('mixture_1000_'+scenario+'.parquet'),index=False)
            portfolios.append(dict(scenario=scenario,capital=1000,actual500plus500=True,periods=periods(mixed,plan),
                scaled_curves=False,transfers=False,independent_alpha_claim=False))
        else:
            portfolios.append(dict(scenario=scenario,capital=1000,actual500plus500=False,periods=None,
                state='UNAVAILABLE_500_CARRY_INCOMPLETE',scaled_curves=False,transfers=False,independent_alpha_claim=False))
        for capital in [1000,500]:
            carry=next(r for r in rows if r['role']=='carry'and r['capital']==capital and r['scenario']==scenario)
            benchmark=next(r for r in rows if r['role']=='benchmark'and r['capital']==capital and r['scenario']==scenario)
            p=carry['periods']
            if p is None:
                gates.append(dict(capital=capital,scenario=scenario,frozen_financial_gate=False,
                    state='INCOMPLETE',return_unknown=True))
                continue
            positive=all(x['net_return']>0 for x in p)
            beats=p[0]['net_return']>benchmark['periods'][0]['net_return']
            risk=p[0]['hourly_mark_drawdown']<=.10
            gates.append(dict(capital=capital,scenario=scenario,positive_all_slices=positive,beats_spot=beats,
                drawdown_le10=risk,full_hedge=carry['full_common_window_hedge'],
                frozen_financial_gate=positive and beats and risk and carry['full_common_window_hedge']))
    result=dict(planned_native_leg_wallets=24,actual_native_leg_wallets=len(legs),
        failed_preserved_in_v1=1,failed_v2=0,not_started=0,
        complete_composite_cases=sum(r['periods']is not None for r in rows),native_fills=2*len(legs),
        leg_hour_points=len(legs)*len(grid),maximum_independent_nav_error_usdt=maximum,legs=legs,
        accounts=rows,portfolios=portfolios,gates=gates,all_frozen_numeric_gates_passed=all(x['frozen_financial_gate']for x in gates),
        new_accepted_independent_alphas=0,independent_oos=False,private_settlement_certified=False,
        source_account_replication=False,objective_achieved=False,orders_enabled=False,services_changed=False)
    save(HERE/'results.json',result)
    print('AUDITED',len(legs),'nativelegwallets;',2*len(legs),'fills;',len(legs)*len(grid),'hourpoints;maxNAVerror',maximum)
    for row in rows:
        print(row['role'],row['capital'],row['scenario'],[(round(p['net_return']*100,5),round(p['hourly_mark_drawdown']*100,5))for p in row['periods']]if row['periods']else'UNKNOWN',row['full_common_window_hedge'])
    for row in portfolios:print('500+500',row['scenario'],row['periods'][0]if row['periods']else'UNAVAILABLE')
    print('FROZEN GATES',gates)


if __name__=='__main__':main()
