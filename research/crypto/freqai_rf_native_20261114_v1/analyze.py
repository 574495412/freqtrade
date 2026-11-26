"""Actual native model/account artifacts, maturity and independent cash checks."""
import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine,metrics,read_native
from research.crypto.dual_thrust_native_20261114_v1.reconcile import ledger
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import audit_trades,save,sha
from research.crypto.tsmom_risk_native_20261114_v1.analyze import inputs,require
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

HERE=Path(__file__).resolve().parent


def periods(nav,plan):
    return [dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']])) for p in plan['periods']]


def scalar_states(fields):
    state=0;result=[]
    for row in fields.itertuples():
        if row.date<pd.Timestamp('2025-01-01T00:00Z'):state=0
        elif state:
            own=row.up if state==1 else row.down
            if own<.5:state=0
        else:
            if row.up>=.6:state=1
            elif row.down>=.6:state=-1
        result.append(state)
    return np.array(result,dtype=int)


def models(path,features,prices):
    directory=path/'freqai_audit';records=[];predictions=[];sizes=[];feature_counts=[]
    for meta in sorted(directory.glob('model_*.json')):
        key=meta.stem.split('_')[1];record=json.loads(meta.read_text())
        train=pd.read_parquet(directory/f'train_{key}.parquet')
        predict=pd.read_parquet(directory/f'predict_{key}.parquet')
        stop=pd.Timestamp(record['train_stop'])
        require((train.date+pd.Timedelta(hours=21)).le(stop).all(),'Unmatured actual training labels')
        require(predict.date.ge(stop).all(),'Prediction precedes model training boundary')
        now=prices.reindex(pd.DatetimeIndex(train.date)).to_numpy()
        future=prices.reindex(pd.DatetimeIndex(train.date)+pd.Timedelta(hours=20)).to_numpy()
        require(np.isfinite(now).all() and np.isfinite(future).all(),'Unknown raw training label price')
        require(np.array_equal(np.where(future>now,'up','down'),train.label.to_numpy()),'Actual classifier target differs from raw market prices')
        columns=[c for c in train if c not in ['date','label']]
        matrix=train[columns].to_numpy()
        require(np.isfinite(matrix).all(),'Unknown actual scaled training feature')
        require(np.allclose(matrix.min(axis=0),-1,atol=1e-10) and np.allclose(matrix.max(axis=0),1,atol=1e-10),'Native training scaler bounds differ')
        require(record['mature_label_check_passed'] and record['independent_prediction_reconciliation_passed'],'Model audit incomplete')
        require(record['maximum_probability_error']<1e-12,'Independent probability mismatch')
        require(record['training_rows']==len(train) and record['prediction_rows']==len(predict),'Model rows drift')
        require(predict.do_predict.eq(1).all(),'Skipped or uncertain prediction rows')
        predictions.append(predict[['date','up','down','do_predict']]);records.append(record)
        sizes.append(len(train));feature_counts.append(len(columns))
    require(len(records)==87,'Native rolling model count differs from full window')
    all_predictions=pd.concat(predictions,ignore_index=True).sort_values('date').reset_index(drop=True)
    actual=features.loc[features.date.ge(pd.Timestamp('2025-01-01T00:00Z')),['date','up','down','do_predict']].reset_index(drop=True)
    pd.testing.assert_frame_equal(all_predictions,actual,check_exact=True,check_dtype=False)
    require(len(actual)==14592 and actual.date.is_unique,'Incomplete/duplicate prediction calendar')
    state=scalar_states(features)
    require(np.array_equal(state,features.target_weight.to_numpy()),'Independent probability intent transitions differ')
    gold_future=prices.reindex(pd.DatetimeIndex(actual.date)+pd.Timedelta(hours=20)).to_numpy()
    gold_now=prices.reindex(pd.DatetimeIndex(actual.date)).to_numpy();known=np.isfinite(gold_future)&np.isfinite(gold_now)
    y=(gold_future[known]>gold_now[known]).astype(int);score=actual.up.to_numpy()[known]
    ranks=pd.Series(score).rank(method='average').to_numpy();positive=int(y.sum());negative=len(y)-positive
    auc=(float(ranks[y==1].sum())-positive*(positive+1)/2)/(positive*negative)
    result=dict(passed=True,native_windows=len(records),train_rows_min=min(sizes),train_rows_max=max(sizes),
                feature_counts=sorted(set(feature_counts)),mature_training_labels=sum(sizes),prediction_rows=len(actual),
                independent_probability_max_error=max(r['maximum_probability_error'] for r in records),
                fully_mature_diagnostic_rows=len(y),tail_unknown_rows=int((~known).sum()),brier_score=float(np.mean((score-y)**2)),
                auc=float(auc),direction_accuracy=float(np.mean((score>=.5)==y)),
                scope='Overlapping20hour historical rolling labels, not independent OOS or net account returns')
    return result,all_predictions


def policy(candidate,path,stats,features,weights):
    selected=features.clock.between(pd.Timestamp('2025-01-01T00:00Z'),pd.Timestamp('2026-08-31T23:00Z'))
    expected=features.loc[selected].set_index('clock')[['target_weight']].rename(columns={'target_weight':'BTC/USDT:USDT'}).astype(float)
    pd.testing.assert_frame_equal(weights,expected,check_exact=True)
    by_clock=features.set_index('clock');cash=float(candidate['capital'])
    for trade in sorted(stats['trades'],key=lambda t:t['open_timestamp']):
        require(len(trade['orders'])==2,'Unexpected position adjustment')
        entry,exit=trade['orders'];when=pd.Timestamp(entry['order_filled_timestamp'],unit='ms',tz='UTC')
        require(when.minute==0 and when.hour not in [0,8,16],'Entry at forbidden funding hour')
        target=by_clock.at[when,'target_weight'];require(target==(-1 if trade['is_short'] else 1),'Entry intent/side differs')
        require(entry['amount']*entry['safe_price']<=.99*cash+1e-5,'Entry above frozen native cash policy')
        if trade['exit_reason']=='exit_signal':
            finish=pd.Timestamp(exit['order_filled_timestamp'],unit='ms',tz='UTC')
            require(finish.minute==0 and finish.hour not in [0,8,16],'Normal exit funding boundary')
            require(by_clock.at[finish,'target_weight']!=target,'Normal exit contradicts intent')
        cash+=trade['profit_abs']
    require(abs(cash-stats['final_balance'])<1e-5,'Terminal cash identity')
    return dict(passed=True,source_weights=weights.size,actual_entry_side_and_cash_checked=len(stats['trades']),
                exit_reasons=dict(Counter(t['exit_reason'] for t in stats['trades'])),no_quantity_adjustments=True)


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    require(json.loads((HERE/'execution_complete.json').read_text())['accounts']==16,'Incomplete financial matrix')
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');data=inputs(plan,grid)
    prices=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-1h-futures.feather').set_index('date').close
    rows=[];navs={};probabilities={};audits=[];maximum_nav=0;trades_count=0;fills=0;model_count=0
    for candidate in plan['candidates']:
        verify(plan,candidate)
        for cost in plan['total_cost_bps_per_side']:
            key=f'{candidate["name"]}_{cost}bps';path=out/key
            receipt=json.loads((path/'process_receipt.json').read_text())
            require(receipt['returncode']==0 and sha(path/'result.json')==receipt['result_sha256'],'Native process terminal state')
            stats,archive,config=read_native(path,candidate['strategy'])
            require(config['fee']==cost/10000 and config['margin_mode']=='isolated' and stats['dry_run_wallet']==candidate['capital'],'Account contract changed')
            require(stats['max_open_trades']==1 and all(t['leverage']==1 for t in stats['trades']),'Leverage/concurrency drift')
            frame=pd.read_parquet(path/'hourly_equity.parquet');require(pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid),'Missing account hours')
            cash=audit_trades(stats);marked=ledger(stats,frame,candidate['capital'],data,expected_leverage=1.)
            features=pd.read_parquet(path/'native_features.parquet');weights=pd.read_parquet(path/'native_weights.parquet')
            rules=policy(candidate,path,stats,features,weights)
            model=None
            if candidate['strategy'].startswith('Rolling'):
                model,prob=models(path,features,prices);model_count+=model['native_windows']
                if candidate['strategy'] in probabilities:pd.testing.assert_frame_equal(probabilities[candidate['strategy']],prob,check_exact=True)
                else:probabilities[candidate['strategy']]=prob
            nav=frame.set_index('date').hourly_mark_equity;navs[key]=nav;p=periods(nav,plan)
            native=json.loads((path/'result.json').read_text())
            for a,b in zip(p,native['periods'],strict=True):
                require(abs(a['net_return']-b['net_return'])<1e-12 and abs(a['maximum_hourly_mark_drawdown']-b['hourly_mark_drawdown'])<1e-12,'Independent full/continuous-year metrics')
            audit=dict(cash=cash,marked=marked,policy=rules,model=model,public_native_model_reconciled=True,private_settlement=False,
                       archive_sha256=sha(archive),auditor_sha256=sha(__file__))
            save(path/'independent_audit.json',audit)
            rows.append(dict(account=candidate['name'],strategy=candidate['strategy'],capital=candidate['capital'],cost_bps_per_side=cost,
                             periods=p,trades=len(stats['trades']),funding_usdt=sum(t['funding_fees'] for t in stats['trades']),model=model,separate_terminal_return=stats['profit_total']))
            maximum_nav=max(maximum_nav,marked['maximum_nav_error_usdt']);trades_count+=len(stats['trades']);fills+=marked['filled_orders'];audits.append(dict(account=key,archive=str(archive),sha256=sha(archive)))
    combinations=[]
    for cost in plan['total_cost_bps_per_side']:
        pairs={}
        for name in ['RollingRF','RollingPrior']:
            nav=combine(navs[f'{name}_500_{cost}bps'],navs[f'SmaPriceBTC_500_{cost}bps']);p=periods(nav,plan);pairs[name]=p
            pd.DataFrame(dict(date=nav.index,hourly_mark_equity=nav.to_numpy())).to_parquet(HERE/f'combo_{name}_{cost}bps.parquet',index=False)
        controls=[r for r in rows if r['capital']==1000 and r['cost_bps_per_side']==cost];rf=next(r for r in controls if r['strategy']=='RollingRF')
        gates=dict(rf_positive_each_slice=all(p['net_return']>0 for p in rf['periods']),combo_positive_each_slice=all(p['net_return']>0 for p in pairs['RollingRF']),
                   rf_beats_all_matched_controls=all(rf['periods'][0]['net_return']>r['periods'][0]['net_return'] for r in controls if r!=rf),
                   rf_mix_beats_prior_mix=pairs['RollingRF'][0]['net_return']>pairs['RollingPrior'][0]['net_return'],
                   rf_mix_beats_best_1000_component=pairs['RollingRF'][0]['net_return']>max(r['periods'][0]['net_return'] for r in controls if r['strategy'] in ['RollingRF','SmaPriceBTC']),
                   drawdown=max(rf['periods'][0]['maximum_hourly_mark_drawdown'],pairs['RollingRF'][0]['maximum_hourly_mark_drawdown'])<=.5)
        combinations.append(dict(cost_bps_per_side=cost,rf_mix=pairs['RollingRF'],prior_mix=pairs['RollingPrior'],gates=gates,necessary_gate_passed=all(gates.values())))
    result=dict(accounts=rows,combinations=combinations,complete_native_accounts=16,model_window_fits=model_count,
                actual_native_trades=trades_count,actual_native_fills=fills,account_hour_points=len(grid)*16,
                maximum_independent_nav_error_usdt=maximum_nav,all_model_and_account_checks_passed=True,
                cross_capital_cost_predictions_exact=True,incumbent_followup_required=all(c['necessary_gate_passed'] for c in combinations),
                accepted_new_factors=0,independent_oos=False,private_settlement=False,objective_achieved=False,evidence=audits)
    save(HERE/'results.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['accounts','evidence']},indent=2))


if __name__=='__main__':main()
