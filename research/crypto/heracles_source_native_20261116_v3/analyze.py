"""Independent actual-fill cash/NAV audit and preregistered wallet combinations."""

import hashlib
import io
import json
import math
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def metrics(nav):
    assert nav.index.is_unique and np.isfinite(nav).all() and (nav>0).all()
    daily=nav.reindex(pd.date_range(nav.index[0],nav.index[-1],freq='24h')).pct_change(fill_method=None).dropna()
    sd=daily.std(ddof=1)
    years=(nav.index[-1]-nav.index[0]).total_seconds()/(365*86400)
    return {'net_return':float(nav.iloc[-1]/nav.iloc[0]-1),'initial_usdt':float(nav.iloc[0]),
            'ending_usdt':float(nav.iloc[-1]),'cagr_365day':float((nav.iloc[-1]/nav.iloc[0])**(1/years)-1),
            'hourly_mark_drawdown':float((1-nav/nav.cummax()).max()),
            'daily_nav_sharpe':float(daily.mean()/sd*math.sqrt(365)) if sd>0 else None,
            'calendar_hours':len(nav),'complete_24h_returns':len(daily)}


def periods(nav,plan):
    return [{'name':p['name'],**metrics(nav.loc[p['start']:p['end']])} for p in plan['periods']]


def native_stats(directory,strategy):
    paths=list((directory/'native_results').glob('*.zip'));assert len(paths)==1
    with ZipFile(paths[0]) as archive:
        name=next(n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json'))
        stats=json.loads(archive.read(name))['strategy'][strategy]
        wallet=pd.read_feather(io.BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('_wallet.feather')))))
    return stats,paths[0],wallet


def account_audit(candidate,plan,grid):
    directory=Path(plan['output_root'])/(candidate['name']+f"_{candidate['cost_bps']}bps")
    receipt=read(directory/'process_receipt.json')
    assert receipt['returncode']==0 and sha(directory/'result.json')==receipt['result_sha256']
    stats,archive,wallet=native_stats(directory,candidate['strategy'])
    cfg=read(directory/'effective_config.json')
    assert cfg['dry_run_wallet']==candidate['capital'] and cfg['fee']==candidate['cost_bps']/10000
    assert cfg['stoploss']==-.256 and cfg['timeframe']=='4h' and cfg['startup_candle_count']==100
    assert cfg['max_open_trades']==2 and cfg['trading_mode']=='spot'
    assert cfg['order_types']['entry']==cfg['order_types']['exit']=='limit'
    for name,r in candidate['source_files'].items():assert sha(directory/'strategies'/name)==r['sha256']
    phase=read(directory/'terminal_phase.json')
    assert pd.Timestamp(phase['last_common_snapshot'])==grid[-1]
    assert pd.Timestamp(phase['last_actual_detail_clock'])>=grid[-1]
    frame=pd.read_parquet(directory/'hourly_equity.parquet')
    assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid)
    clocks=grid.as_unit('ms').asi8
    nav=np.full(len(grid),float(candidate['capital']))
    counts=np.zeros(len(grid),dtype=int)
    quantities={p:np.zeros(len(grid)) for p in candidate['pairs']}
    cash=np.full(len(grid),float(candidate['capital']))
    marks={p:pd.read_feather(Path(plan['datadir'])/(p.replace('/','_')+'-1h.feather')).set_index('date').open.reindex(grid).to_numpy() for p in candidate['pairs']}
    max_cash_error=0.;fills=historical_fills=natural=forced=0
    gross=paid=exit_reserve=closed_rounding=D(0)
    terminal_orders=[]
    for trade in stats['trades']:
        assert not trade['is_short'] and trade['leverage']==1 and not trade['funding_fees']
        assert trade['fee_open']==trade['fee_close']==cfg['fee'] and len(trade['orders'])==2
        entries=[o for o in trade['orders'] if o['ft_is_entry']]
        exits=[o for o in trade['orders'] if not o['ft_is_entry']]
        assert len(entries)==len(exits)==1
        entry,exit_order=entries[0],exits[0]
        assert entry['order_filled_timestamp']>=clocks[0]
        quantity=D(str(entry['amount']));bought=quantity*D(str(entry['safe_price']))
        sold=D(str(exit_order['amount']))*D(str(exit_order['safe_price']))
        assert quantity==D(str(exit_order['amount']))
        opening_fee=D(str(trade['fee_open']));closing_fee=D(str(trade['fee_close']))
        complete=sold*(1-closing_fee)-bought*(1+opening_fee)
        difference=D(str(trade['profit_abs']))-complete
        max_cash_error=max(max_cash_error,float(abs(difference)))
        assert abs(difference)<D('0.000001')
        forced_exit=trade['exit_reason']=='force_exit'
        if forced_exit:
            matching=[r for r in phase['open_trades_before_terminal'] if r['pair']==trade['pair']
                      and pd.Timestamp(r['open_date'])==pd.Timestamp(trade['open_date']) and r['has_open_position']]
            assert len(matching)==1
            assert exit_order['ft_order_tag']=='force_exit'
            forced+=1
            terminal_orders.append({'pair':trade['pair'],'raw_force_date':trade['close_date'],
                                    'actual_post_loop_phase_after':phase['last_actual_detail_clock']})
        else:
            assert exit_order['order_filled_timestamp']>=entry['order_filled_timestamp']
            natural+=int(exit_order['order_filled_timestamp']<clocks[-1])
        active=clocks>entry['order_filled_timestamp']
        closed=(clocks>exit_order['order_filled_timestamp']) if not forced_exit else np.zeros(len(grid),dtype=bool)
        held=(active & ~closed).astype(float)
        price=marks[trade['pair']]
        q=float(quantity);buy=float(bought);sell=float(sold)
        correction=float(difference)*closed
        cash += -buy*(1+float(opening_fee))*active+sell*(1-float(closing_fee))*closed+correction
        nav += -buy*(1+float(opening_fee))*active+sell*(1-float(closing_fee))*closed+q*price*held*(1-float(closing_fee))+correction
        quantities[trade['pair']] += q*held
        counts += held.astype(int)
        historical_fills += int(entry['order_filled_timestamp']<clocks[-1])
        historical_fills += int(not forced_exit and exit_order['order_filled_timestamp']<clocks[-1])
        fills+=2
        # Independent common-endpoint attribution; never a frictionless rerun.
        if active[-1]:
            mark=D(str(price[-1]))
            gross += sold-bought if closed[-1] else quantity*mark-bought
            paid += bought*opening_fee+(sold*closing_fee if closed[-1] else 0)
            exit_reserve += quantity*mark*closing_fee if held[-1] else 0
            closed_rounding += difference if closed[-1] else 0
    assert forced==len([r for r in phase['open_trades_before_terminal'] if r['has_open_position']])
    error=float(np.max(np.abs(nav-frame.hourly_mark_equity.to_numpy())))
    assert error<3e-5,error
    assert np.array_equal(counts,frame.open_trades.to_numpy())
    for pair,price in marks.items():assert np.isfinite(price).all() and quantities[pair].min()>=0
    assert abs(float(nav[-1]-candidate['capital'])-float(gross-paid-exit_reserve+closed_rounding))<3e-5
    assert abs(stats['final_balance']-candidate['capital']-stats['profit_total_abs'])<1e-6
    actual=pd.Series(nav,index=grid,name='hourly_mark_equity')
    computed=periods(actual,plan);reported=read(directory/'result.json')['periods']
    for a,b in zip(computed,reported,strict=True):
        for key in ['net_return','hourly_mark_drawdown','daily_nav_sharpe']:
            assert abs(a[key]-b[key])<1e-10,(key,a[key],b[key])
    assert abs((1+computed[1]['net_return'])*(1+computed[2]['net_return'])-1-computed[0]['net_return'])<1e-12
    # Bind raw native wallet balances at their actual4h capture clocks. Native
    # quote cash excludes remaining entry-fee recognition until realization.
    assert not wallet.duplicated(['date','currency']).any()
    quote=wallet.loc[wallet.currency.eq('USDT')].copy()
    valid=quote.date.isin(grid)
    quote=quote.loc[valid]
    assert len(quote)==3648
    qidx=grid.get_indexer(pd.DatetimeIndex(quote.date))
    remaining_entry_fee=np.zeros(len(grid))
    for trade in stats['trades']:
        ent=trade['orders'][0]
        active=clocks>ent['order_filled_timestamp']
        if trade['exit_reason']!='force_exit':active &= clocks<=trade['orders'][-1]['order_filled_timestamp']
        remaining_entry_fee += active*ent['amount']*ent['safe_price']*trade['fee_open']
    native_quote_expected=cash+remaining_entry_fee
    quote_error=float(np.max(np.abs(quote.balance.to_numpy()-native_quote_expected[qidx])))
    assert quote_error<3e-5,quote_error
    base_rows_checked=0
    maximum_base_quantity_error=0.
    for pair in candidate['pairs']:
        base=wallet.loc[wallet.currency.eq(pair.split('/')[0]) & wallet.date.isin(grid)].copy()
        indices=grid.get_indexer(pd.DatetimeIndex(base.date))
        assert np.array_equal(base.rate.to_numpy(),marks[pair][indices])
        err=float(np.max(np.abs(base.balance.to_numpy()-quantities[pair][indices]))) if len(base) else 0.
        assert err<1e-10,err
        maximum_base_quantity_error=max(maximum_base_quantity_error,err)
        base_rows_checked+=len(base)
        missing=set(qidx)-set(indices)
        assert all(quantities[pair][i]==0 for i in missing)
    primary=pd.Series(frame.hourly_mark_equity.to_numpy(),index=grid,name='hourly_mark_equity')
    native_periods=periods(primary,plan)
    result={'account':candidate['name'],'capital':candidate['capital'],'scenario':candidate['scenario'],
            'periods':native_periods,'actual_native_trades':len(stats['trades']),'actual_fills':fills,
            'common_endpoint_fills':historical_fills,'naturally_closed_before_endpoint':natural,
            'post_loop_terminal_force_exits':forced,'terminal_orders':terminal_orders,
            'hour_points':len(grid),'maximum_independent_nav_error_usdt':error,
            'maximum_per_trade_cash_error_usdt':max_cash_error,
            'native_quote_rows_audited':len(quote),'native_base_rows_audited':base_rows_checked,
            'maximum_native_base_quantity_error':maximum_base_quantity_error,'maximum_native_quote_error_usdt':quote_error,
            'common_gross_price_pnl_usdt':float(gross),'paid_quote_fees_usdt':float(paid),
            'remaining_exit_fee_reserve_usdt':float(exit_reserve),'closed_pnl_rounding_usdt':float(closed_rounding),
            'archive_sha256':sha(archive),'independent_candidate_imports':False,'passed':True}
    save(directory/'independent_account_audit.json',result)
    pd.DataFrame({'date':grid,'hourly_mark_equity':primary.to_numpy()}).to_parquet(HERE/(candidate['name']+'.parquet'),index=False)
    return primary,result


def main():
    plan=read(HERE/'plan.json')
    assert read(HERE/'execution_complete.json')['accounts']==4
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns')
    navs={};accounts=[]
    for candidate in plan['candidates']:
        nav,result=account_audit(candidate,plan,grid)
        navs[candidate['name']]=nav;accounts.append(result)
    baseline=HERE.parent/'nfi_carry_matched_native_20261115_v1'
    nfi_plan=read(baseline/'plan.json');carry_root=HERE.parent/'paired_carry_reservation_native_20261115_v2'
    controls={};references=[]
    for scenario,cost in [('conservative',20),('lower',13)]:
        for capital in [1000,500]:
            for role in ['nfi_spot','carry']:
                name=f'{role}_{capital}_{scenario}'
                path=(Path(nfi_plan['output_root'])/(name+f'_{cost}bps')/'hourly_equity.parquet') if role=='nfi_spot' else carry_root/(name+'.parquet')
                frame=pd.read_parquet(path)
                assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid)
                controls[name]=pd.Series(frame.hourly_mark_equity.to_numpy(),index=grid)
                assert controls[name].iloc[0]==capital
                references.append({'name':name,'path':str(path),'sha256':sha(path),'reused_not_new_wallet':True})
    gates=[];portfolios=[]
    for row in accounts:
        if row['capital']!=1000:continue
        scenario=row['scenario'];full=row['periods'][0];baseline_full=metrics(controls[f'nfi_spot_1000_{scenario}'])
        passed=(full['net_return']>baseline_full['net_return'] and full['hourly_mark_drawdown']<=.25
                and full['daily_nav_sharpe']>.5 and all(p['net_return']>0 for p in row['periods'][1:])
                and row['naturally_closed_before_endpoint']>=30)
        gates.append({'account':row['account'],'passed':bool(passed),'matched_nfi_spot_1000':baseline_full})
    for scenario in ['conservative','lower']:
        sleeve=navs[f'heracles_500_{scenario}'];whole=navs[f'heracles_1000_{scenario}']
        for partner in ['nfi_spot','carry']:
            nav=sleeve+controls[f'{partner}_500_{scenario}']
            name=f'heracles_{partner}_1000_{scenario}'
            parts=periods(nav,plan);a=metrics(whole);b=metrics(controls[f'{partner}_1000_{scenario}'])
            passed=(parts[0]['net_return']>max(a['net_return'],b['net_return'])
                    and parts[0]['hourly_mark_drawdown']<=max(a['hourly_mark_drawdown'],b['hourly_mark_drawdown']))
            pd.DataFrame({'date':grid,'hourly_mark_equity':nav.to_numpy()}).to_parquet(HERE/(name+'.parquet'),index=False)
            portfolios.append({'name':name,'scenario':scenario,'actual500plus500':True,'new_native_wallet':False,
                               'mixed_market_hedged_component':partner=='carry','periods':parts,'passed':bool(passed)})
    results={'new_completed_native_wallets':4,'preserved_failed_financial_attempts':2,
             'actual_native_trades':sum(r['actual_native_trades'] for r in accounts),
             'actual_fills':sum(r['actual_fills'] for r in accounts),
             'common_endpoint_fills':sum(r['common_endpoint_fills'] for r in accounts),
             'new_hour_points':sum(r['hour_points'] for r in accounts),
             'maximum_independent_nav_error_usdt':max(r['maximum_independent_nav_error_usdt'] for r in accounts),
             'maximum_native_quote_error_usdt':max(r['maximum_native_quote_error_usdt'] for r in accounts),
             'accounts':accounts,'standalone_gates':gates,'portfolios':portfolios,'control_references':references,
             'new_accepted_independent_alphas':0,'new_registry_definitions':0,'new_factor_analysis':0,'new_vibe_benches':0,
             'independent_oos':False,'author_account_reproduced':False,'higher_comparable_return_established':False,
             'goal_complete':False,'orders_enabled':False,'production_eligible':False}
    save(HERE/'results.json',results)
    print('AUDIT PASS',results['actual_native_trades'],'trades',results['actual_fills'],'fills',flush=True)
    for p in portfolios:print(p['name'],p['periods'][0]['net_return'],p['passed'],flush=True)


if __name__=='__main__':main()
