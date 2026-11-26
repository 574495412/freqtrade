"""Audit actual native model accounts; strict real-sleeve comparisons only."""
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine,metrics,read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save,sha
from research.crypto.tsmom_risk_native_20261114_v1.analyze import check_account,inputs,require
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

HERE=Path(__file__).resolve().parent


def capacity(plan,candidate,path):
    with gzip.open(path/'strategy_trace.json.gz','rt') as stream:trace=json.load(stream)
    weights=pd.read_parquet(path/'native_weights.parquet')
    prices={p:pd.read_feather(Path(plan['datadir'])/'futures'/f'{p.split("/")[0]}_USDT_USDT-1h-futures.feather').set_index('date').open for p in plan['pairs']}
    active=zero=0;desired=actual=0.;by_pair={p:dict(active=0,zero_after_rules=0) for p in plan['pairs']}
    for record in trace['budgets']:
        clock=pd.Timestamp(record['clock']);nav=record['native_marked_equity']
        for pair in plan['pairs']:
            weight=abs(float(weights.at[clock.floor('D'),pair]));qty=record['target_quantities'][pair]
            if weight>1e-12:
                active+=1;by_pair[pair]['active']+=1
                if qty==0:zero+=1;by_pair[pair]['zero_after_rules']+=1
            desired+=.99*nav*weight;actual+=qty*float(prices[pair].loc[clock])
    return dict(nonzero_weight_asset_days=active,zero_quantity_after_exchange_rules=zero,
                zero_fraction_of_active=zero/active if active else None,
                target_notional_retained_ratio=actual/desired if desired else None,by_pair=by_pair,
                scope='Desired daily targets, not actual intraday holdings or an alpha measure')


def audit_source_weights(plan,candidate,path):
    strategy=candidate['strategy'];out=Path(plan['output_root'])
    weights=pd.read_parquet(path/'native_weights.parquet')
    if strategy=='TrendGrossOne':
        return dict(passed=True,scope='Exact preflight weights and native quantity/cash rules; existing source math reused')
    actual=json.loads((path/'allocation_diagnostics.json').read_text())
    frozen=json.loads((out/f'preflight_diagnostics_{strategy}.json').read_text())
    require(actual==frozen,'Native-selected pair models/states differ from preflight')
    close=pd.read_parquet(out/'spot_daily.parquet')
    check=np.zeros(weights.shape);maximum_z_error=0.;regression_errors=[];cells=0
    for model in actual['selected_pairs']:
        dep,indep=model['dependent'],model['independent'];alpha,beta=model['alpha'],model['beta']
        train=np.log(close.loc['2020-08-18':'2024-01-16',[dep,indep]])
        x=train[indep].to_numpy();y=train[dep].to_numpy();mx=math.fsum(x)/len(x);my=math.fsum(y)/len(y)
        slope=math.fsum((a-mx)*(b-my) for a,b in zip(x,y,strict=True))/math.fsum((a-mx)**2 for a in x)
        intercept=my-slope*mx
        regression_errors.extend([abs(slope-beta),abs(intercept-alpha)])
        state=0
        rows=[r for r in actual['states'] if r['dependent']==dep and r['independent']==indep]
        require(len(rows)==len(weights),'Incomplete source pair state calendar')
        for i,day in enumerate(weights.index):
            last=day-pd.Timedelta(days=1);window=close.loc[last-pd.Timedelta(days=29):last]
            require(len(window)==30,'Independent zscore window incomplete')
            spread=[math.log(a)-alpha-beta*math.log(b) for a,b in zip(window[dep],window[indep],strict=True)]
            mean=math.fsum(spread)/30;sd=math.sqrt(math.fsum((v-mean)**2 for v in spread)/29)
            z=(spread[-1]-mean)/sd
            maximum_z_error=max(maximum_z_error,abs(z-rows[i]['z']))
            if state==0:
                if z>2:state=-1
                elif z<-2:state=1
            elif abs(z)>4 or abs(z)<.5:state=0
            require(state==rows[i]['state'],'Independent source state transition differs')
            share=1/(len(actual['selected_pairs'])*(1+abs(beta)))
            check[i,weights.columns.get_loc(dep+'/USDT:USDT')]+=state*share
            check[i,weights.columns.get_loc(indep+'/USDT:USDT')]-=state*beta*share
            cells+=1
    error=float(np.max(np.abs(check-weights.to_numpy())))
    require(max(regression_errors,default=0)<1e-10 and maximum_z_error<1e-8 and error<1e-12,'Independent source math mismatch')
    return dict(passed=True,selected_pairs=actual['selected_count'],independent_ols_fields=len(regression_errors),
                max_ols_error=max(regression_errors,default=0),independent_pair_day_states=cells,
                max_z_error=maximum_z_error,max_netted_weight_error=error,
                shared_underlying_symbols=len({x for m in actual['selected_pairs'] for x in [m['dependent'],m['independent']]}),
                not_proof_of_independent_pairs=True)


def audit_one(plan,candidate,cost,data,grid):
    key=f'{candidate["name"]}_{cost}bps';path=Path(plan['output_root'])/key
    receipt=json.loads((path/'process_receipt.json').read_text())
    require(receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json'),'Native worker completion/hash')
    verify(plan,candidate)
    stats,archive,cfg=read_native(path,candidate['strategy'])
    require(cfg['fee']==cost/10000 and cfg['dry_run_wallet']==candidate['capital'],'Native cost/capital')
    require(cfg['margin_mode']=='cross' and cfg['exchange']['pair_whitelist']==plan['pairs'],'Native margin/universe')
    frame=pd.read_parquet(path/'hourly_equity.parquet')
    nav=frame.set_index('date').hourly_mark_equity.loc[plan['start']:plan['comparison_end']]
    require(nav.index.as_unit('ns').equals(grid) and nav.iloc[0]==candidate['capital'],'Full common clock/capital')
    cash,marked,policy=check_account(plan,candidate,path,stats,archive,frame,data)
    source=audit_source_weights(plan,candidate,path)
    constraints=capacity(plan,candidate,path)
    periods=[dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']])) for p in plan['periods']]
    row=dict(account=candidate['name'],strategy=candidate['strategy'],cost_bps_per_side=cost,periods=periods,
             native_trades=len(stats['trades']),filled_orders=marked['filled_orders'],
             numeric_gate=all(p['net_return']>0 and p['maximum_hourly_mark_drawdown']<=.5 for p in periods),
             native_terminal_return_separate=stats['profit_total'],actual_settlement='UNAUTHENTICATED',
             gross_mark_exposure_to_nav_max=marked['maximum_gross_mark_exposure_to_nav'],
             abs_net_mark_exposure_to_nav_max=marked['maximum_abs_net_mark_exposure_to_nav'])
    row['capacity_diagnostic']=constraints
    audit=dict(account=key,archive=str(archive),archive_sha256=sha(archive),cash=cash,marked=marked,policy=policy,source=source)
    save(path/'pair_source_audit.json',source)
    print('AUDITED',key,'return',periods[0]['net_return'],'NAV error',marked['maximum_nav_error_usdt'],flush=True)
    return nav,row,audit


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    require(json.loads((HERE/'execution_complete.json').read_text())['accounts']==12,'Incomplete matrix')
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');data=inputs(plan,grid)
    # A missing theoretical8h observation is not automatically assigned zero.
    expected=pd.date_range(plan['start'],plan['comparison_end'],freq='8h').as_unit('ms').asi8
    missing={p:np.setdiff1d(expected,x['times']).tolist() for p,x in data.items()}
    require(not any(missing.values()),'Expected source-universe funding event absent')
    navs={};rows=[];audits=[]
    for candidate in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            nav,row,audit=audit_one(plan,candidate,cost,data,grid)
            navs[audit['account']]=nav;rows.append(row);audits.append(audit)
    combinations=[];gates=[]
    for cost in plan['total_cost_bps_per_side']:
        standalone={r['strategy']:r for r in rows if r['account'].endswith('_1000') and r['cost_bps_per_side']==cost}
        for selector in ['SourceNaivePairs','AdjustedEGPairs']:
            nav=combine(navs[f'{selector}_500_{cost}bps'],navs[f'TrendGrossOne_500_{cost}bps'])
            nav.to_frame('hourly_mark_equity').to_parquet(HERE/f'combo_{selector}_{cost}bps.parquet')
            periods=[dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']])) for p in plan['periods']]
            best=max(standalone[s]['periods'][0]['net_return'] for s in [selector,'TrendGrossOne'])
            pair_beats_trend=standalone[selector]['periods'][0]['net_return']>standalone['TrendGrossOne']['periods'][0]['net_return']
            combo_beats_best=periods[0]['net_return']>best
            positive=all(p['net_return']>0 and p['maximum_hourly_mark_drawdown']<=.5 for p in periods)
            gate=standalone[selector]['numeric_gate'] and pair_beats_trend and positive and combo_beats_best
            combinations.append(dict(selector=selector,cost_bps_per_side=cost,actual_sleeves=[selector+'_500','TrendGrossOne_500'],
                                      periods=periods,numeric_gate=gate,daily_return_correlation=float(navs[f'{selector}_500_{cost}bps'].resample('D').last().pct_change().corr(navs[f'TrendGrossOne_500_{cost}bps'].resample('D').last().pct_change()))))
            gates.append(dict(selector=selector,cost_bps_per_side=cost,pair_positive_full_and_years=standalone[selector]['numeric_gate'],
                              pair_beats_trend=pair_beats_trend,combination_positive_full_and_years=positive,
                              combination_beats_best_component=combo_beats_best,combined_numeric_gate=gate))
    save(HERE/'native_validation.json',dict(passed=True,accounts=12,audits=audits,
         total_native_fills=sum(a['marked']['filled_orders'] for a in audits),hourly_account_points=12*len(grid),
         full8h_funding_grid_present=True,actual_private_settlement_certified=False))
    save(HERE/'results.json',dict(accounts=rows,combinations=combinations,gates=gates,
         independent_oos=False,source_unit_notional_account_reproduced=False,new_accepted_independent_factors=0,
         accepted_deployable_strategy=False,objective_complete=False))


if __name__=='__main__':main()
