"""Audit actual native artifacts, policy, cash, funding and independently funded sums."""
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics, read_native
from research.crypto.spread_liquidity_native_20261114_v1.native_runner import verify
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import audit_trades, save, sha

HERE=Path(__file__).resolve().parent


def require(value, reason):
    if not value:raise ValueError(reason)


def load_inputs(plan, grid):
    inputs={}
    for pair in plan['pairs']:
        sym=pair.split('/')[0]
        base=Path(plan['datadir'])/f'futures/{sym}_USDT_USDT'
        marks=pd.read_feather(str(base)+'-1h-mark.feather').set_index('date').open
        marks=marks.reindex(grid)
        funds=pd.read_feather(str(base)+'-1h-funding_rate.feather')
        funds=funds.loc[funds.date.ge(pd.Timestamp(plan['start'])) & funds.date.lt(pd.Timestamp(plan['end']))]
        fund_marks=marks.reindex(pd.DatetimeIndex(funds.date))
        require(len(funds)==1824 and np.isfinite(fund_marks).all(),'Incomplete funding/mark calendar')
        times=pd.DatetimeIndex(funds.date).as_unit('ms').asi8
        unit=funds.funding_rate.to_numpy()*fund_marks.to_numpy()
        inputs[pair]={'marks':marks.to_numpy(),'times':times,'unit':unit}
    return inputs


def ledger_audit(stats, frame, capital, inputs):
    """Reconcile observed NAV from exported actual fills, not counterfactual orders."""
    times=pd.DatetimeIndex(frame.date).as_unit('ms').asi8
    marked=np.full(len(frame),float(capital))
    gross=np.zeros(len(frame));net=np.zeros(len(frame));count=np.zeros(len(frame),dtype=int)
    funding_errors=[]
    ownership={pair:np.zeros(len(v['times']),dtype=int) for pair,v in inputs.items()}
    for trade in stats['trades']:
        pair=trade['pair'];data=inputs[pair]
        opened,closed=trade['open_timestamp'],trade['close_timestamp']
        require(len(trade['orders'])==2 and trade['leverage']==1,'Unregistered adjustment/leverage')
        entries=[o for o in trade['orders'] if o['ft_is_entry']]
        exits=[o for o in trade['orders'] if not o['ft_is_entry']]
        require(len(entries)==len(exits)==1,'Entry/full exit order shape')
        quantity=entries[0]['amount'];basis=quantity*entries[0]['safe_price']
        require(quantity==exits[0]['amount']==trade['amount'],'Quantity changed within lifecycle')
        active=(times>opened)&(times<=closed)
        done=times>closed
        sign=-1 if trade['is_short'] else 1
        fundmask=(data['times']>=opened)&(data['times']<=closed)
        expected=-sign*math.fsum(data['unit'][fundmask])*quantity
        funding_errors.append(abs(expected-trade['funding_fees']))
        ownership[pair]+=fundmask.astype(int)
        marked[done]+=trade['profit_abs']
        if active.any():
            prices=data['marks'][active]
            require(np.isfinite(prices).all(),'Missing actual-held mark')
            gross[active]+=quantity*prices
            net[active]+=sign*quantity*prices
            count[active]+=1
            # State before the current hour's funding/order processing.
            event_times=data['times'][fundmask]
            event_values=-sign*quantity*data['unit'][fundmask]
            cumulative=np.concatenate([[0.],np.cumsum(event_values)])
            accrued=cumulative[np.searchsorted(event_times,times[active],side='left')]
            marked[active]+=sign*(quantity*prices-basis)-basis*trade['fee_open']-quantity*prices*trade['fee_close']+accrued
    nav_error=np.max(np.abs(marked-frame.hourly_mark_equity.to_numpy()))
    require(nav_error<2e-5,f'Actual fill/mark/funding NAV reconstruction: {nav_error}')
    # Observer.gross uses 5m futures OPEN, rather than the independent hourly MARK.
    require(np.array_equal(count,frame.open_trades.to_numpy()),'Actual-held position counts')
    require(max(funding_errors,default=0)<1e-8,'Native funding scalar reconciliation')
    dual=sum(int((v>1).sum()) for v in ownership.values())
    require(dual==0,'Native event attributed to two same-pair lifecycles')
    ratio=gross/frame.hourly_mark_equity.to_numpy()
    signed=net/frame.hourly_mark_equity.to_numpy()
    return {'passed':True,'hourly_points':len(frame),'maximum_nav_error_usdt':float(nav_error),
        'maximum_per_trade_funding_error_usdt':max(funding_errors,default=0),
        'funding_events':sum(len(v['times']) for v in inputs.values()),
        'assigned_event_lifecycles':sum(int(v.sum()) for v in ownership.values()),'dual_owned_events':dual,
        'maximum_gross_mark_exposure_to_nav':float(ratio.max()),
        'maximum_abs_net_mark_exposure_to_nav':float(np.abs(signed).max()),
        'market_beta_neutrality_certified':False,'private_funding_settlement_certified':False}


def policy_audit(candidate,path,stats,plan):
    native=pd.read_parquet(path/'native_features.parquet').sort_values(['pair','decision_date']).reset_index(drop=True)
    pre=pd.read_parquet(Path(plan['output_root'])/f'preflight_features_{candidate["strategy"]}.parquet').sort_values(['pair','decision_date']).reset_index(drop=True)
    pd.testing.assert_frame_equal(native,pre,check_exact=True,check_dtype=False)
    target=json.loads((path/'native_targets.json').read_text())
    expected=json.loads((Path(plan['output_root'])/f'preflight_targets_{candidate["strategy"]}.json').read_text())
    require(target==expected,'Native daily targets differ from actual native preflight')
    field={'EdgeLiquidity':'edge30','AmihudLiquidity':'amihud30','LowVolBaseline':'lowvol63'}[candidate['strategy']]
    observations=0
    for pair in plan['pairs']:
        symbol=pair.split('/')[0]
        snap=pd.read_parquet(path/f'native_indicators_{symbol}.parquet')
        snap=snap.loc[snap.date.ge(pd.Timestamp(plan['start']))]
        values=native.loc[native.pair.eq(pair)].set_index('decision_date')[field]
        days=snap.date.dt.floor('D')
        reference=values.reindex(pd.DatetimeIndex(days)).to_numpy()
        require(np.array_equal(reference,snap.factor_value.to_numpy(),equal_nan=True),'Native hourly factor mapping')
        chosen=[target[t.strftime('%Y-%m-%d')][pair] for t in days]
        require(np.array_equal(chosen,snap.daily_target.to_numpy()),'Native hourly target mapping')
        observations+=len(snap)
    trace=json.loads((path/'strategy_trace.json').read_text())
    accepted={(r['pair'],pd.Timestamp(r['clock']).value//10**6):r for r in trace['accepted']}
    require(len(accepted)==len(trace['accepted'])==len(stats['trades']),'Accepted entry/fill count')
    for trade in stats['trades']:
        opened=pd.Timestamp(trade['open_date']);closed=pd.Timestamp(trade['close_date'])
        require(opened.hour==1 and opened.minute==opened.second==0,'Late entry')
        desired=target[opened.strftime('%Y-%m-%d')][trade['pair']]
        require(desired==(-1 if trade['is_short'] else 1),'Entry not source rank target')
        rec=accepted[(trade['pair'],trade['open_timestamp'])]
        require(rec['amount']==trade['amount'] and rec['rate']==trade['open_rate'],'Native accepted quantity/price')
        require(trade['amount']*trade['open_rate']<=rec['budget']+.01,'Minimum stake upsizing')
        if trade['exit_reason']=='daily_rank_change':
            require(closed.hour==1 and closed.minute==closed.second==0,'Late source exit')
            require(target[closed.strftime('%Y-%m-%d')][trade['pair']]!=desired,'Unchanged-rank churn')
        else:require(trade['exit_reason'] in {'force_exit','stop_loss','liquidation'},'Unexpected exit reason')
        # Every intermediate daily decision keeps the same direction; exits use native timing.
        decisions=pd.date_range(opened.floor('D')+pd.Timedelta(days=1),closed.floor('D'),freq='D')
        for day in decisions:
            if day+pd.Timedelta(hours=1)>=closed:continue
            require(target[day.strftime('%Y-%m-%d')][trade['pair']]==desired,'Missed native rank-change exit')
    return {'passed':True,'exact_native_feature_cells':len(native)*3,
        'hourly_factor_and_target_rows':observations,'actual_filled_entries':len(stats['trades']),
        'native_trace_attempts':len(trace['attempts']),'exit_reasons':dict(Counter(t['exit_reason'] for t in stats['trades'])),
        'same_side_quantity_carries':True,'late_entries_or_minimum_upsizing':0}


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    require(json.loads((HERE/'execution_complete.json').read_text())['accounts']==12,'Incomplete matrix')
    require(json.loads((HERE/'feature_audit.json').read_text())['passed'],'Independent input audit')
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns')
    inputs=load_inputs(plan,grid)
    rows=[];navs={};evidence=[];gates=[]
    for candidate in plan['candidates']:
        verify(plan,candidate)
        for cost in plan['total_cost_bps_per_side']:
            key=f'{candidate["name"]}_{cost}bps';path=out/key
            receipt=json.loads((path/'process_receipt.json').read_text())
            require(receipt['returncode']==0 and sha(path/'result.json')==receipt['result_sha256'],'Native completion/hash')
            stats,archive,config=read_native(path,candidate['strategy'])
            require(stats['dry_run_wallet']==candidate['capital'] and config['fee']==cost/10000,'Actual cash/cost')
            require(stats['timeframe']=='1h' and stats['max_open_trades']==2 and stats['trading_mode']=='futures' and config['timeframe_detail']=='5m','Native account contract')
            cash=audit_trades(stats)
            frame=pd.read_parquet(path/'hourly_equity.parquet')
            require(pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid),'Incomplete common clock')
            nav=frame.set_index('date').hourly_mark_equity;nav.index=grid
            require(nav.iloc[0]==candidate['capital'],'Initial marked equity')
            full=metrics(nav);result=json.loads((path/'result.json').read_text())
            require(abs(full['net_return']-result['periods'][0]['net_return'])<1e-12,'Endpoint return mismatch')
            require(abs(full['maximum_hourly_mark_drawdown']-result['periods'][0]['hourly_mark_drawdown'])<1e-12,'Peak-walk drawdown mismatch')
            ledger=ledger_audit(stats,frame,candidate['capital'],inputs)
            policy=policy_audit(candidate,path,stats,plan)
            for name,value in [('independent_cash_audit',cash),('independent_ledger_audit',ledger),('policy_audit',policy)]:save(path/(name+'.json'),value)
            periods={p['name']:metrics(nav.loc[p['start']:p['end']]) for p in plan['periods']}
            row={'account':candidate['name'],'strategy':candidate['strategy'],'capital':candidate['capital'],'cost_bps_per_side':cost,
                **full,'periods':periods,'native_trades':len(stats['trades']),
                'native_terminal_return_separate':stats['profit_total'],'funding_net_usdt':math.fsum(t['funding_fees'] for t in stats['trades']),
                'gross_mark_exposure_max':ledger['maximum_gross_mark_exposure_to_nav'],
                'net_mark_exposure_abs_max':ledger['maximum_abs_net_mark_exposure_to_nav']}
            rows.append(row);navs[(candidate['strategy'],candidate['capital'],cost)]=nav
            evidence.append({'account':key,'native_exit_code':receipt['returncode'],'archive':str(archive),'archive_sha256':sha(archive),
                'nav_sha256':sha(path/'hourly_equity.parquet'),'result_sha256':sha(path/'result.json'),
                'cash_passed':cash['passed'],'ledger':ledger,'policy':policy})
    combos=[]
    for cost in plan['total_cost_bps_per_side']:
        nav=combine(navs[('EdgeLiquidity',500,cost)],navs[('LowVolBaseline',500,cost)])
        nav.rename('equity').to_frame().to_parquet(HERE/f'EdgeLowVol_{cost}bps.parquet')
        full=metrics(nav);periods={p['name']:metrics(nav.loc[p['start']:p['end']]) for p in plan['periods']}
        standalone=[r for r in rows if r['capital']==1000 and r['cost_bps_per_side']==cost]
        best=max(r['net_return'] for r in standalone)
        passed=full['net_return']>best and full['net_return']>0 and full['maximum_hourly_mark_drawdown']<=.5 and all(p['net_return']>0 for p in periods.values())
        combos.append({'account':'Edge500+LowVol500','cost_bps_per_side':cost,**full,'periods':periods,'best_standalone_1000_return':best,'numeric_gate':passed})
        edge=next(r for r in standalone if r['strategy']=='EdgeLiquidity')
        controls=[r for r in standalone if r['strategy']!='EdgeLiquidity']
        edge_pass=edge['net_return']>0 and edge['maximum_hourly_mark_drawdown']<=.5 and all(p['net_return']>0 for p in edge['periods'].values()) and all(edge['net_return']>c['net_return'] for c in controls)
        gates.append({'cost_bps_per_side':cost,'edge_numeric_gate':edge_pass,'combo_numeric_gate':passed})
    save(HERE/'results.json',{'native_accounts':rows,'funded_combinations':combos,'gates':gates,
        'native_account_count':12,'new_hourly_nav_observations':len(grid)*12,
        'both_cost_numeric_pass':all(g['edge_numeric_gate'] and g['combo_numeric_gate'] for g in gates),
        'source_secret_filter_reproduced':False,'independent_oos_certified':False,'production_eligible':False})
    save(HERE/'native_validation.json',{'passed':True,'accounts':evidence,
        'actual_native_trades':sum(r['native_trades'] for r in rows),'hourly_points':len(grid)*12,
        'factor_and_target_rows':sum(e['policy']['hourly_factor_and_target_rows'] for e in evidence)})
    print(json.dumps({'accounts':12,'new_nav_points':len(grid)*12,'gates':gates,'combinations':[{k:v for k,v in c.items() if k!='periods'} for c in combos]}))


if __name__=='__main__':main()
