"""Audit real native archives and full hourly wallets before return comparisons."""

import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine,metrics,read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import audit_trades,save,sha
from research.crypto.tsmom_risk_native_20261114_v1.analyze import inputs,require
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify
from research.crypto.dual_thrust_native_20261114_v1.reconcile import ledger

HERE=Path(__file__).resolve().parent


def periods(nav,plan):
    result=[]
    for period in plan['periods']:
        part=nav.loc[period['start']:period['end']]
        require(part.index[0]==pd.Timestamp(period['start']) and part.index[-1]==pd.Timestamp(period['end']),'Period boundary missing')
        result.append(dict(name=period['name'],**metrics(part)))
    return result


def policy(plan,candidate,path,stats):
    pair=plan['pairs'][0];strategy=candidate['strategy']
    expected=pd.read_parquet(Path(plan['output_root'])/f'preflight_weights_{strategy}.parquet')
    actual=pd.read_parquet(path/'native_weights.parquet')
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
    features=pd.read_parquet(path/'native_features.parquet')
    prior=pd.read_parquet(Path(plan['output_root'])/f'preflight_features_{strategy}.parquet')
    columns=['date','clock','upper','lower','range','sma_ratio','source_signal','target_weight']
    pd.testing.assert_frame_equal(features[columns],prior[columns],check_exact=True)
    targets=json.loads((path/'native_targets.json').read_text())
    require(targets=={str(d):{pair:int(v)} for d,v in actual[pair].items()},'Native target contract differs')
    fields=features.set_index('clock')
    with gzip.open(path/'strategy_trace.json.gz','rt') as stream:trace=json.load(stream)
    require(len(trace['accepted'])==len(stats['trades']),'Unreconciled accepted entry requests')
    cash=float(candidate['capital']);checked=0
    for trade in sorted(stats['trades'],key=lambda t:t['open_timestamp']):
        require(len(trade['orders'])==2,'Unexpected quantity reset or adjustment')
        entry,exit=trade['orders']
        when=pd.Timestamp(entry['order_filled_timestamp'],unit='ms',tz='UTC')
        require(when.minute==0 and when.hour not in [0,8,16],'Entry at forbidden funding boundary')
        require(fields.at[when,'target_weight']==1,'Entry without completed long intent')
        require(entry['amount']*entry['safe_price']<=.99*cash+1e-5,'Entry exceeds frozen native cash policy')
        if trade['exit_reason']=='exit_signal':
            finish=pd.Timestamp(exit['order_filled_timestamp'],unit='ms',tz='UTC')
            require(finish.minute==0 and finish.hour not in [0,8,16],'Signal exit at funding boundary')
            require(fields.at[finish,'target_weight']==0,'Exit without completed flat intent')
        cash+=trade['profit_abs'];checked+=1
    require(abs(cash-stats['final_balance'])<1e-5,'Terminal cash mismatch')
    return dict(passed=True,actual_weight_cells=actual.size,actual_feature_rows=len(features),
                checked_native_lifecycles=checked,exit_reasons=dict(Counter(t['exit_reason'] for t in stats['trades'])),
                source_quantity_rebalancing_replication=False)


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    require(json.loads((HERE/'execution_complete.json').read_text())['accounts']==12,'Incomplete native matrix')
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns')
    data=inputs(plan,grid)
    require(set(pd.to_datetime(data['BTC/USDT:USDT']['times'],unit='ms',utc=True).hour)<={0,8,16},'Unexpected funding clock needs a new endpoint audit')
    navs={};rows=[];evidence=[];max_nav=0;fills=0;lifecycles=0
    for candidate in plan['candidates']:
        verify(plan,candidate)
        for cost in plan['total_cost_bps_per_side']:
            name=f'{candidate["name"]}_{cost}bps';path=out/name
            receipt=json.loads((path/'process_receipt.json').read_text())
            require(receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json'),'Terminal worker/result mismatch')
            stats,archive,config=read_native(path,candidate['strategy'])
            require(config['fee']==cost/10000 and config['margin_mode']=='isolated' and stats['max_open_trades']==1,'Archive account contract drift')
            require(stats['dry_run_wallet']==candidate['capital'],'Actual native capital drift')
            require(all(not t['is_short'] and t['leverage']==1 for t in stats['trades']),'Direction or leverage drift')
            frame=pd.read_parquet(path/'hourly_equity.parquet')
            require(pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid),'Incomplete full hourly wallet')
            cash=audit_trades(stats);marked=ledger(stats,frame,candidate['capital'],data,expected_leverage=1.)
            rules=policy(plan,candidate,path,stats)
            nav=frame.set_index('date').hourly_mark_equity
            navs[name]=nav;period=periods(nav,plan)
            result=json.loads((path/'result.json').read_text())
            for expected,native in zip(period,result['periods'],strict=True):
                require(abs(expected['net_return']-native['net_return'])<1e-12,'Independent return mismatch')
                require(abs(expected['maximum_hourly_mark_drawdown']-native['hourly_mark_drawdown'])<1e-12,'Independent DD mismatch')
            save(path/'independent_account_audit.json',dict(cash=cash,marked=marked,policy=rules,public_model_full_window_reconciled=True,
                authenticated_exchange_settlement=False,archive_sha256=sha(archive),auditor_sha256=sha(__file__)))
            rows.append(dict(account=candidate['name'],strategy=candidate['strategy'],capital=candidate['capital'],cost_bps_per_side=cost,
                             periods=period,native_lifecycles=len(stats['trades']),native_funding_usdt=sum(t['funding_fees'] for t in stats['trades']),
                             separate_terminal_return=stats['profit_total'],production_eligible=False))
            evidence.append(dict(account=name,archive=str(archive),archive_sha256=sha(archive),audit=sha(path/'independent_account_audit.json')))
            fills+=marked['filled_orders'];lifecycles+=len(stats['trades']);max_nav=max(max_nav,marked['maximum_nav_error_usdt'])
    combos=[]
    for cost in plan['total_cost_bps_per_side']:
        nav=combine(navs[f'SourceDualBTC_500_{cost}bps'],navs[f'SmaPriceBTC_500_{cost}bps'])
        pd.DataFrame(dict(date=nav.index,hourly_mark_equity=nav.to_numpy())).to_parquet(HERE/f'combo_{cost}bps.parquet',index=False)
        p=periods(nav,plan)
        controls=[r for r in rows if r['capital']==1000 and r['cost_bps_per_side']==cost]
        dual=next(r for r in controls if r['strategy']=='SourceDualBTC')
        candidate_positive=all(m['net_return']>0 for m in dual['periods'])
        beat_controls=all(dual['periods'][0]['net_return']>r['periods'][0]['net_return'] for r in controls if r!=dual)
        combo_positive=all(m['net_return']>0 for m in p)
        combo_beats=max(r['periods'][0]['net_return'] for r in controls if r['strategy']!='HoldBTC')<p[0]['net_return']
        drawdown=p[0]['maximum_hourly_mark_drawdown']<=.5 and dual['periods'][0]['maximum_hourly_mark_drawdown']<=.5
        combos.append(dict(cost_bps_per_side=cost,periods=p,candidate_positive_all_slices=candidate_positive,
                           candidate_beats_matched_controls=beat_controls,combo_positive_all_slices=combo_positive,
                           combo_beats_best_1000_component=combo_beats,drawdown_gate=drawdown,
                           necessary_numeric_gate_passed=all([candidate_positive,beat_controls,combo_positive,combo_beats,drawdown])))
    result=dict(accounts=rows,combinations=combos,complete_native_accounts=12,native_lifecycles=lifecycles,native_fills=fills,
                account_hour_points=12*len(grid),maximum_independent_nav_error_usdt=max_nav,
                all_accounts_and_orders_reconciled=True,incumbent_followup_required=all(x['necessary_numeric_gate_passed'] for x in combos),
                new_accepted_factors=0,source_spot_account_replication=False,historical_master_pit=False,
                private_settlement_certified=False,independent_oos=False,objective_achieved=False,evidence=evidence)
    save(HERE/'results.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['accounts','evidence']},indent=2))


if __name__=='__main__':main()
