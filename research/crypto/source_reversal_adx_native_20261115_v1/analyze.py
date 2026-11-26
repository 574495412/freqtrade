"""Independent actual-order, mark and funding reconstruction; no candidate imports."""
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


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def save(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=str,allow_nan=False)+'\n')


def native(directory,strategy):
    archives=list((directory/'native_results').glob('*.zip'));assert len(archives)==1
    with ZipFile(archives[0])as archive:
        name=next(n for n in archive.namelist()if n.endswith('.json')and not n.endswith('_config.json'))
        config=next(n for n in archive.namelist()if n.endswith('_config.json'))
        return json.loads(archive.read(name))['strategy'][strategy],json.loads(archive.read(config)),archives[0]


def inputs(plan,grid):
    result={}
    for pair in plan['pairs']:
        stem=Path(plan['datadir'])/'futures'/pair.replace('/','_').replace(':','_')
        marks=pd.read_feather(str(stem)+'-1h-mark.feather').set_index('date').open.reindex(grid)
        funds=pd.read_feather(str(stem)+'-1h-funding_rate.feather')
        funds=funds.loc[funds.date.ge(pd.Timestamp(plan['start']))&funds.date.lt(pd.Timestamp(plan['end']))]
        times=pd.DatetimeIndex(funds.date)
        assert len(times)==1824 and np.isfinite(marks).all()
        unit=funds.funding_rate.to_numpy()*marks.reindex(times).to_numpy()
        assert np.isfinite(unit).all()
        result[pair]=dict(marks=marks.to_numpy(),times=times.as_unit('ms').asi8,unit=unit)
    return result


def metrics(nav):
    v=nav.to_numpy();assert np.isfinite(v).all()and(v>0).all()
    daily_grid=pd.date_range(nav.index[0],nav.index[-1],freq='24h')
    daily=nav.reindex(daily_grid).pct_change(fill_method=None).dropna()
    deviation=float(daily.std(ddof=1))
    years=(nav.index[-1]-nav.index[0]).total_seconds()/(365*24*3600)
    return dict(net_return=float(v[-1]/v[0]-1),cagr_365day=float((v[-1]/v[0])**(1/years)-1),
        maximum_hourly_mark_drawdown=float((1-v/np.maximum.accumulate(v)).max()),
        initial_usdt=float(v[0]),ending_usdt=float(v[-1]),observations=len(v),
        complete_24h_returns=len(daily),daily_nav_sharpe=float(daily.mean()/deviation*math.sqrt(365))if deviation>0 else None)


def periods(nav,plan):
    return [dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]))for p in plan['periods']]


def ledger(stats,frame,capital,data):
    clock=pd.DatetimeIndex(frame.date).as_unit('ms').asi8
    total=np.full(len(clock),float(capital));counts=np.zeros(len(clock),dtype=int)
    ownership={p:np.zeros(len(d['times']),dtype=int)for p,d in data.items()}
    closed_total=D(0);max_trade=max_funding=0.;fills=0;entry_boundary=exit_boundary=0
    entry_cash_sensitivity=exit_cash_sensitivity=0.
    for trade in stats['trades']:
        assert not trade['is_short']and trade['leverage']==1
        orders=trade['orders'];assert len(orders)==2 and orders[0]['ft_is_entry']and not orders[1]['ft_is_entry']
        entry,exit=orders;assert D(str(entry['amount']))==D(str(exit['amount']))
        qty=float(entry['amount']);assert qty>0
        begin,end=int(entry['order_filled_timestamp']),int(exit['order_filled_timestamp']);assert begin<=end
        a=D(str(entry['amount']))*D(str(entry['safe_price']));b=D(str(exit['amount']))*D(str(exit['safe_price']))
        cash=b-a-a*D(str(trade['fee_open']))-b*D(str(trade['fee_close']))+D(str(trade['funding_fees']))
        error=float(abs(cash-D(str(trade['profit_abs']))));assert error<.00001
        max_trade=max(max_trade,error);closed_total+=cash;fills+=2
        events=data[trade['pair']];active=(events['times']>=begin)&(events['times']<=end)
        # The owning native exchange uses inclusive open/close endpoints.
        # That is a model convention, not authenticated settlement ordering.
        funding=np.where(active,-qty*events['unit'],0.)
        expected=math.fsum(funding.tolist());error=abs(expected-trade['funding_fees'])
        assert error<1e-7,(trade['pair'],begin,end,error)
        max_funding=max(max_funding,error);ownership[trade['pair']]+=active.astype(int)
        at_entry=events['times']==begin;at_exit=events['times']==end
        entry_boundary+=int((active&at_entry).sum());exit_boundary+=int((active&at_exit).sum())
        entry_cash_sensitivity-=float(funding[at_entry].sum())
        exit_cash_sensitivity-=float(funding[at_exit].sum())
        bought=np.where(clock>begin,float(a),0.);sold=np.where(clock>end,float(b),0.)
        held=qty*((clock>begin).astype(int)-(clock>end).astype(int))
        accrued=np.concatenate([[0.],np.cumsum(funding)])[np.searchsorted(events['times'],clock,side='left')]
        total+=sold+held*events['marks']-bought-bought*trade['fee_open']-sold*trade['fee_close']-held*events['marks']*trade['fee_close']+accrued
        counts+=(held>0).astype(int)
    assert abs(float(closed_total)-stats['profit_total_abs'])<.0001
    assert abs(capital+float(closed_total)-stats['final_balance'])<.0001
    error=float(np.max(np.abs(total-frame.hourly_mark_equity.to_numpy())))
    assert error<3e-5,error
    assert np.array_equal(counts,frame.open_trades.to_numpy())
    return dict(passed=True,maximum_nav_error_usdt=error,maximum_trade_cash_error_usdt=max_trade,
        maximum_funding_error_usdt=max_funding,native_lifecycles=len(stats['trades']),filled_orders=fills,
        hourly_points=len(clock),same_clock_entry_events=entry_boundary,same_clock_exit_events=exit_boundary,
        dual_owned_native_funding_events=sum(int((v>1).sum())for v in ownership.values()),
        fixed_fills_cash_sensitivity_exclude_entry_boundary_usdt=entry_cash_sensitivity,
        fixed_fills_cash_sensitivity_exclude_exit_boundary_usdt=exit_cash_sensitivity,
        sensitivity_is_full_account_rerun=False,authenticated_settlement=False)


def policy(directory,stats,plan,candidate):
    with gzip.open(directory/'strategy_trace.json.gz','rt')as stream:trace=json.load(stream)
    accepted=trace['confirmed'];used=[];unused=list(range(len(accepted)))
    markets=read(plan['market_snapshot']['path'])['markets']
    for trade in stats['trades']:
        order=trade['orders'][0];stamp=pd.Timestamp(order['order_filled_timestamp'],unit='ms',tz='UTC')
        hits=[i for i in unused if accepted[i]['pair']==trade['pair']and pd.Timestamp(accepted[i]['at'])==stamp
              and abs(accepted[i]['quantity']-order['amount'])<1e-8]
        assert len(hits)==1,(trade['pair'],stamp,hits)
        item=accepted[hits[0]];unused.remove(hits[0]);used.append(item)
        assert order['amount']*order['safe_price']<=item['budget']+1e-7
        assert abs(order['safe_price']-item['rate'])<1e-8
        tick=D(str(markets[trade['pair']]['precision']['amount']))
        assert D(str(order['amount']))%tick==0
        for event in trade['orders']:
            price_tick=D(str(markets[trade['pair']]['precision']['price']))
            assert D(str(event['safe_price']))%price_tick==0,(trade['pair'],event)
    assert not unused,'Unfilled confirmed entry needs separate explicit accounting'
    observed=read(directory/'native_signal_receipts.json');expected=read(HERE/'preflight_signal_receipts.json')
    for key,value in observed.items():assert value==expected[key],key
    assert set(observed)=={candidate['strategy']+'|'+p for p in plan['pairs']}
    precision=read(directory/'price_precision_receipt.json')
    assert precision['ticks']=={p:markets[p]['precision']['price']for p in plan['pairs']}
    skipped=[r for r in trace['budgets']if not r['admitted']]
    return dict(passed=True,checked_entries=len(used),actual_native_signals_match_preflight=True,
        minimum_rejected_attempts=len(skipped),minimum_rejections_by_pair={p:sum(r['pair']==p for r in skipped)for p in plan['pairs']},
        price_tick_contract='Fixed registered snapshot, historical changes uncertified',no_minimum_upsizing=True)


def main():
    plan=read(HERE/'plan.json');out=Path(plan['output_root'])
    assert read(HERE/'execution_complete.json')['accounts']==14
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');assert len(grid)==14592
    data=inputs(plan,grid);rows=[];navs={};evidence=[];fills=trades=0;maximum=0.
    for candidate in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            name=f'{candidate["name"]}_{cost}bps';directory=out/name
            process=read(directory/'process_receipt.json')
            assert process['returncode']==0 and process['result_sha256']==sha(directory/'result.json')
            stats,config,archive=native(directory,candidate['strategy'])
            assert stats['dry_run_wallet']==candidate['capital']and stats['max_open_trades']==8
            assert config['fee']==cost/10000 and config['margin_mode']=='isolated'
            assert config['startup_candle_count']==1000
            frame=pd.read_parquet(directory/'hourly_equity.parquet')
            assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid)
            cash=ledger(stats,frame,candidate['capital'],data)
            allocation=policy(directory,stats,plan,candidate)
            nav=frame.set_index('date').hourly_mark_equity;assert nav.iloc[0]==candidate['capital']
            p=periods(nav,plan);result=read(directory/'result.json')
            for a,b in zip(p,result['periods'],strict=True):
                assert abs(a['net_return']-b['net_return'])<1e-12
                assert abs(a['maximum_hourly_mark_drawdown']-b['hourly_mark_drawdown'])<1e-12
                assert a['complete_24h_returns']==b['complete_24h_returns']
                if a['daily_nav_sharpe']is not None:assert abs(a['daily_nav_sharpe']-b['daily_nav_sharpe'])<1e-12
            assert abs((1+p[1]['net_return'])*(1+p[2]['net_return'])-1-p[0]['net_return'])<1e-12
            save(directory/'independent_account_audit.json',dict(ledger=cash,policy=allocation,
                archive_sha256=sha(archive),auditor_sha256=sha(__file__),public_model_full_window_reconciled=True,
                private_execution_or_historical_filter_certification=False))
            rows.append(dict(account=candidate['name'],strategy=candidate['strategy'],capital=candidate['capital'],cost_bps_per_side=cost,
                periods=p,native_lifecycles=len(stats['trades']),native_funding_usdt=math.fsum(t['funding_fees']for t in stats['trades']),
                minimum_rejected_attempts=allocation['minimum_rejected_attempts'],separate_native_terminal_return=stats['profit_total']))
            navs[name]=nav;evidence.append(dict(account=name,archive=str(archive),sha256=sha(archive),audit_sha256=sha(directory/'independent_account_audit.json')))
            fills+=cash['filled_orders'];trades+=len(stats['trades']);maximum=max(maximum,cash['maximum_nav_error_usdt'])
    combinations=[];gates=[]
    for cost in plan['total_cost_bps_per_side']:
        for left,right in [('SourceReversal','SourceADX'),('SourceReversal','ReversalInADXTrend')]:
            a=navs[f'{left}_500_{cost}bps'];b=navs[f'{right}_500_{cost}bps']
            assert a.index.equals(b.index)and a.iloc[0]==b.iloc[0]==500
            nav=a+b;name=f'{left}_plus_{right}_{cost}bps'
            pd.DataFrame(dict(date=nav.index,hourly_mark_equity=nav.to_numpy())).to_parquet(HERE/(name+'.parquet'),index=False)
            combinations.append(dict(name=name,cost_bps_per_side=cost,periods=periods(nav,plan),
                actual500_wallets=True,transfers_or_rebalancing=False,allocation_alpha_claim=False))
        controls=[r for r in rows if r['capital']==1000 and r['cost_bps_per_side']==cost]
        interaction=next(r for r in controls if r['strategy']=='ReversalInADXTrend');p=interaction['periods']
        positive=all(v['net_return']>0 for v in p)
        beat=all(p[0]['net_return']>r['periods'][0]['net_return']for r in controls if r is not interaction)
        risk=p[0]['maximum_hourly_mark_drawdown']<=.30
        sharpe=p[0]['daily_nav_sharpe']is not None and p[0]['daily_nav_sharpe']>.5
        gates.append(dict(cost_bps_per_side=cost,positive_all_slices=positive,beats_all_three_controls=beat,
            hourly_drawdown_le30=risk,daily_sharpe_gt_05=sharpe,necessary_numeric_gate_passed=positive and beat and risk and sharpe))
    result=dict(accounts=rows,combinations=combinations,interaction_gates=gates,
        complete_native_accounts=14,native_lifecycles=trades,native_fills=fills,account_hour_points=14*len(grid),
        maximum_independent_nav_error_usdt=maximum,supplied_declared_funding_events=8*1824,
        numeric_gate_all_costs=all(r['necessary_numeric_gate_passed']for r in gates),
        new_accepted_factors=0,source_spot_account_replication=False,independent_oos=False,
        private_settlement_certified=False,historical_market_filters_certified=False,
        objective_achieved=False,orders_enabled=False,services_changed=False,evidence=evidence)
    save(HERE/'results.json',result)
    print('AUDITED14actualnativeaccounts',trades,'lifecycles',fills,'fills;',14*len(grid),'hourlypoints;maxerror',maximum)
    for row in rows:
        if row['capital']==1000:
            p=row['periods'];print(row['strategy'],row['cost_bps_per_side'],[(round(x['net_return']*100,4),round(x['maximum_hourly_mark_drawdown']*100,4))for x in p])
    print('FROZEN GATES',gates)


if __name__=='__main__':main()
