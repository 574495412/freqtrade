"""Audit native fills/NAV; read-only diagnostics are not a replacement engine."""
import json,zipfile
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
import numpy as np
import pandas as pd
from .prepare import HERE,ROOT,OUT,sha,save
from .native_runner import verify,audit_trades
from research.crypto.band_trend_native_20261113_v1.analyze import read_native,metrics

def funding_inputs(plan):
    old=json.loads((ROOT/'research/crypto/session_reversal_native_20261113_v1/funding_audit.json').read_text())
    source=next(r for r in old['records']if r['symbol']=='BTC');frames=[]
    for receipt in source['archives']:
        path=Path(receipt['path']);assert sha(path)==receipt['sha256']
        check=path.with_suffix('.CHECKSUM');assert sha(check)==receipt['checksum_sha256'] and check.read_text().split()[0]==sha(path)
        with zipfile.ZipFile(path)as archive:frames.append(pd.read_csv(archive.open(archive.namelist()[0])))
    raw=pd.concat(frames,ignore_index=True)
    times=pd.DatetimeIndex(pd.to_datetime(raw.calc_time,unit='ms',utc=True)).floor('h')
    expected=pd.date_range(plan['start'],plan['end'],freq='8h',inclusive='left')
    assert times.as_unit('ns').equals(expected.as_unit('ns')) and raw.funding_interval_hours.eq(8).all()
    native=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-1h-funding_rate.feather').set_index('date').funding_rate.reindex(times)
    assert np.allclose(native,raw.last_funding_rate,rtol=0,atol=1e-12)
    mark=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-1h-mark.feather').set_index('date').open
    return {'hours':times.as_unit('ms').asi8,'public':raw.calc_time.to_numpy(dtype=np.int64),'unit':raw.last_funding_rate.to_numpy()*mark.reindex(times).to_numpy(),'marks':mark,'archives':source['archives']}

def audit_policy(candidate,stats,plan,prices):
    trades=sorted(stats['trades'],key=lambda t:t['open_timestamp'])
    passive=candidate['strategy']=='ConstantMinuteControl';phase=0 if candidate['strategy']=='BoundaryStartMinute'else 14
    opens={t['open_timestamp']:t for t in trades};assert len(opens)==len(trades)
    if passive:
        assert len(trades)==1 and trades[0]['open_timestamp']==int(pd.Timestamp(plan['start']).timestamp()*1000)+60000
        assert trades[0]['exit_reason']=='force_exit'
        return {'passed':True,'filled':1,'scheduled':1,'unfunded':0,'holding':'Native continuous long; terminal force exit separate.'}
    grid=pd.date_range(pd.Timestamp(plan['start'])+pd.Timedelta(minutes=1),pd.Timestamp(plan['end'])-pd.Timedelta(minutes=1),freq='min')
    grid=grid[grid.minute%15==phase]
    closes=np.array([t['close_timestamp']for t in trades]);profits=np.cumsum([t['profit_abs']for t in trades]);filled=0;rejected=[]
    market=json.loads(Path(plan['market_snapshot']['path']).read_text())['markets']['BTC/USDT:USDT'];step=Decimal(str(market['precision']['amount']))
    for clock in grid:
        ms=int(clock.timestamp()*1000);idx=int(np.searchsorted(closes,ms,side='left'));cash=1000+(float(profits[idx-1])if idx else 0);available=cash*.99;price=float(prices.loc[clock,'open'])
        minimum=max(market['limits']['cost']['min']*1.05/.95,market['limits']['amount']['min']*price*1.05)
        amount=float((Decimal(str(available))/Decimal(str(price))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        if ms not in opens:
            # Reconcile omitted opportunities to actual prior cash and native
            # market minimum; never fill them in a second wallet simulator.
            assert available<minimum or amount==0,('Unexplained omitted intent',str(clock),cash,minimum)
            rejected.append((ms,cash,minimum));continue
        t=opens[ms];assert available>=minimum and abs(t['amount']-amount)<1e-9,(clock,t['amount'],amount)
        assert not t['is_short'] and t['leverage']==1 and len(t['orders'])==2
        assert abs(t['open_rate']-price)<1e-8
        assert t['close_timestamp']-ms==60000 and t['exit_reason']=='one_minute_end'
        assert abs(t['close_rate']-prices.loc[clock+pd.Timedelta(minutes=1),'open'])<1e-8
        filled+=1
    assert filled==len(trades)
    return {'passed':True,'filled':filled,'scheduled':len(grid),'unfunded':len(rejected),'unfunded_reason':'Frozen native minimum stake/quantity given actual previous filled cash; no capital reset/upsizing','first_unfunded':rejected[0]if rejected else None,'last_unfunded':rejected[-1]if rejected else None,'cash_and_lot_reconciled':True}

def audit_hourly(stats,frame,inputs):
    trades=sorted(stats['trades'],key=lambda t:t['open_timestamp']);closed=np.array([t['close_timestamp']for t in trades]);pnl=np.cumsum([t['profit_abs']for t in trades]);maximum=0
    for row in frame.itertuples():
        ms=int(row.date.timestamp()*1000);count=int(np.searchsorted(closed,ms,side='left'));cash=1000+(float(pnl[count-1])if count else 0);opened=[]
        # Nonoverlapping native single-position history means at most the next
        # unclosed trade can exist before the current boundary's orders.
        if count<len(trades):
            t=trades[count]
            if t['open_timestamp']<ms<=t['close_timestamp']:opened=[t]
        for t in opened:
            price=float(inputs['marks'].loc[row.date]);entry=t['amount']*t['open_rate'];mark=t['amount']*price
            accrued=-float(np.sum(inputs['unit'][(inputs['hours']>=t['open_timestamp'])&(inputs['hours']<ms)]))*t['amount']
            cash+=mark-entry-entry*t['fee_open']-mark*t['fee_close']+accrued
        error=abs(cash-row.hourly_mark_equity);maximum=max(maximum,error)
        assert error<1e-5,('Hourly actual-order reconciliation',str(row.date),error)
    return {'passed':True,'points':len(frame),'maximum_actual_order_mark_nav_error_usdt':maximum,'snapshot':'Before orders and funding at exact hourly boundary; prior actual fills only.'}

def audit_funding(stats,inputs):
    totals={k:0. for k in ['native_inclusive','before_boundary_orders','after_boundary_orders','public_timestamp_proxy']};errors=[];ownership=np.zeros(len(inputs['hours']),dtype=int)
    for t in stats['trades']:
        o,c=t['open_timestamp'],t['close_timestamp'];h,p=inputs['hours'],inputs['public']
        masks={'native_inclusive':(h>=o)&(h<=c),'before_boundary_orders':(h>o)&(h<=c),'after_boundary_orders':(h>=o)&(h<c),'public_timestamp_proxy':(p>o)&(p<=c)}
        for model,mask in masks.items():
            value=-float(np.sum(inputs['unit'][mask]))*t['amount'];totals[model]+=value
            if model=='native_inclusive':errors.append(abs(value-t['funding_fees']));ownership+=mask
    assert max(errors,default=0)<1e-8 and ownership.max()<=1
    return {'passed_native_formula':True,'maximum_trade_funding_error_usdt':max(errors,default=0),'fixed_native_fill_funding_usdt':totals,'funding_events_charged':int(ownership.sum()),'dual_lifecycle_events':int((ownership>1).sum()),'not_counterfactual_account_return':True,'actual_private_settlement_certified':False}

def main():
    plan=json.loads((HERE/'plan.json').read_text());assert json.loads((HERE/'execution_complete.json').read_text())['accounts']==6
    prices=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-1m-futures.feather').set_index('date')
    inputs=funding_inputs(plan);rows=[];proof=[];navs={};gates=[]
    for cost in plan['total_cost_bps_per_side']:
        for candidate in plan['candidates']:
            verify(plan,candidate);name=f'{candidate["name"]}_{cost}bps';path=OUT/name
            receipt=json.loads((path/'process_receipt.json').read_text());assert receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json')
            stats,archive,config=read_native(path,candidate['strategy']);assert stats['dry_run_wallet']==1000 and stats['timeframe']=='1m' and stats['max_open_trades']==1 and config['fee']==cost/10000
            assert json.loads((path/'callback_errors.json').read_text())['passed']
            actual=pd.read_parquet(path/'native_indicators.parquet').set_index('date')
            reference=pd.read_parquet(OUT/f'preflight_{candidate["name"]}.parquet').set_index('date')
            fields=['scheduled_entry']
            pd.testing.assert_frame_equal(actual[fields],reference.reindex(actual.index)[fields],check_dtype=False,check_exact=True)
            cash=audit_trades(stats);policy=audit_policy(candidate,stats,plan,prices);funding=audit_funding(stats,inputs)
            frame=pd.read_parquet(path/'hourly_equity.parquet');assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns'))
            hourly=audit_hourly(stats,frame,inputs)
            nav=frame.set_index('date').hourly_mark_equity;navs[name]=nav
            periods=[{'name':p['name'],**metrics(nav.loc[p['start']:p['end']])}for p in plan['periods']]
            result=json.loads((path/'result.json').read_text());assert abs(periods[0]['net_return']-result['periods'][0]['net_return'])<1e-12
            audits={'cash':cash,'policy':policy,'funding':funding,'hourly':hourly};save(path/'independent_audit.json',audits)
            row={'account':candidate['name'],'cost_bps_per_side':cost,'periods':periods,'natural_trades':sum(t['exit_reason']!='force_exit'for t in stats['trades']),'forced_trades':sum(t['exit_reason']=='force_exit'for t in stats['trades']),'unfunded_intents':policy['unfunded'],'native_terminal_return_separate':stats['profit_total'],'native_funding_usdt':sum(t['funding_fees']for t in stats['trades'])}
            rows.append(row);proof.append({'account':name,'native_zip':str(archive),'native_zip_sha256':sha(archive),'actual_trades':len(stats['trades']),'hourly_points':len(frame),'maximum_nav_error':hourly['maximum_actual_order_mark_nav_error_usdt'],'maximum_funding_error':funding['maximum_trade_funding_error_usdt']})
        subset=[r for r in rows if r['cost_bps_per_side']==cost];constant=next(r for r in subset if r['account']=='ConstantMinuteControl')
        for r in subset:
            if r['account']=='ConstantMinuteControl':continue
            passed=all(p['net_return']>0 for p in r['periods']) and r['periods'][0]['maximum_hourly_mark_drawdown']<=.3 and r['periods'][0]['net_return']>constant['periods'][0]['net_return']
            gates.append({'account':r['account'],'cost_bps_per_side':cost,'passed':bool(passed)})
    save(HERE/'comparison.json',{'accounts':rows,'numeric_gates':gates,'ranking':'Only these6same-contract1m1000USDTaccounts; never mixed-source global winner.'})
    save(HERE/'funding_input_recheck.json',{'passed':True,'events':len(inputs['hours']),'archive_receipts':inputs['archives'],'native_hour_bucket_proxy':True})
    save(HERE/'verification.json',{'passed_within_scope':True,'actual_successful_native_accounts':6,'actual_hourly_points':sum(p['hourly_points']for p in proof),'evidence':proof,'source_minute_label_resolved':False,'source_exact_replication':False,'numeric_gates':gates,'new_accepted_alpha':False,'goal_achieved':False,'orders_enabled':False,'services_changed':False})
    print(json.dumps({'accounts':[{k:r[k]for k in ['account','cost_bps_per_side','natural_trades','unfunded_intents']}|r['periods'][0]for r in rows],'gates':gates},indent=2))
if __name__=='__main__':main()
