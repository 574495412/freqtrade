"""Native fills/NAV, actual funded-state transitions and source-level audit."""
import argparse,gzip,hashlib,importlib.util,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from .bootstrap import boot,HERE,ROOT
boot()
from research.crypto.band_trend_native_20261113_v1.analyze import combine,metrics,read_native
from research.crypto.dual_thrust_native_20261114_v1.reconcile import ledger
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import audit_trades
from research.crypto.tsmom_risk_native_20261114_v1.analyze import inputs
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,default=str)+'\n')
def source_policy(kind):
    path=ROOT/'research/crypto/mechanism_source_intake_20261114_v1/sources/profile/strategy.py';name='source_profile_state_audit';spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m.EstrategiaFade(.055,100,'RANGE_EXTREME',32)if kind=='fade'else m.EstrategiaBreakout(.055,100,'RANGE_EXTREME',1,0 if kind=='no_volume'else 1.5)

def state_and_fills(c,path,stats,plan):
    with gzip.open(path/'strategy_trace.json.gz','rt')as f:trace=json.load(f)
    fields=pd.read_parquet(path/'native_features.parquet').drop(columns='pair');expected=pd.read_parquet(Path(plan['output_root'])/'preflight_features.parquet')
    pd.testing.assert_frame_equal(fields,expected,check_exact=True)
    fields=fields.set_index('clock');rows=trace['state'];clocks=pd.DatetimeIndex(pd.to_datetime([r['clock']for r in rows],utc=True));grid=pd.date_range(pd.Timestamp(plan['start'])+pd.Timedelta(minutes=15),pd.Timestamp(plan['end'])-pd.Timedelta(minutes=15),freq='15min')
    assert clocks.as_unit('ns').equals(grid.as_unit('ns'))
    trades=stats['trades'];opens=np.sort([t['open_timestamp']for t in trades]);closes=np.sort([t['close_timestamp']for t in trades]);ticks=clocks.as_unit('ms').asi8;held=np.searchsorted(opens,ticks,side='left')-np.searchsorted(closes,ticks,side='left');assert np.all((held>=0)&(held<=1))
    kind={'ProfileBreakout':'breakout','ProfileFade':'fade','ProfileNoVolume':'no_volume'}[c['strategy']];reference=source_policy(kind);signals=skips=0;maximum=0.;intents=[]
    for record,clock,busy in zip(rows,clocks,held,strict=True):
        assert record['held_native']==bool(busy)
        f=fields.loc[clock];assert pd.Timestamp(record['input_date'])==f.date and f.date+pd.Timedelta(minutes=15)==clock
        if busy:signal=None;skips+=1
        elif kind=='fade':signal=reference.processar_vela(f.high,f.low,f.close,f.vah,f.val,f.poc)
        else:signal=reference.processar_vela(f.close,f.vah,f.val,f.poc,f.volume,f.volume_mean)
        actual=record['intent'];assert(actual is not None)==(signal is not None)
        if actual:
            signals+=1;assert actual['side']==(1 if signal=='COMPRAR'else-1)
            error=max(abs(actual['stop']-reference.sl_pendente),abs(actual['target']-reference.tp_pendente));maximum=max(maximum,error);assert error<1e-10
        permitted=not(clock.minute==0 and clock.hour in [0,8,16]);assert record['clock_permitted']==permitted
        intents.append(0 if actual is None else actual['side'])
    weights=pd.read_parquet(path/'native_weights.parquet');assert pd.DatetimeIndex(weights.index).as_unit('ns').equals(clocks.as_unit('ns'));np.testing.assert_array_equal(weights.iloc[:,0],intents)
    accepted={pd.Timestamp(r['clock']):r for r in trace['accepted']};filled={pd.Timestamp(r['clock']):r for r in trace['fills']};sized={pd.Timestamp(r['clock']):r for r in trace['sizing']}
    raw5=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-5m-futures.feather').set_index('date')
    cash=float(c['capital']);reasons={};entry_bars=0
    for t in sorted(trades,key=lambda t:t['open_timestamp']):
        assert len(t['orders'])==2 and t['leverage']==3
        entry,exit=t['orders'];clock=pd.Timestamp(entry['order_filled_timestamp'],unit='ms',tz='UTC');end=pd.Timestamp(exit['order_filled_timestamp'],unit='ms',tz='UTC')
        assert clock in accepted and clock in filled and clock in sized
        a,p,s=accepted[clock],filled[clock],sized[clock]
        assert abs(entry['amount']-p['amount'])<1e-12 and abs(entry['safe_price']-p['price'])<1e-8
        assert a['side']==('short'if t['is_short']else'long')and clock.minute%15==0 and not(clock.minute==0 and clock.hour in [0,8,16])
        assert abs(s['native_cash_budget']-cash)<1e-5
        assert entry['amount']*abs(entry['safe_price']-p['stop'])<=.01*cash+1e-6
        assert entry['amount']*entry['safe_price']/3<=s['requested_margin']+1e-5
        detail=raw5.loc[end]
        if t['exit_reason']=='profile_target':
            if t['is_short']:assert detail.low<=p['target']and exit['safe_price']<=p['target']+.101
            else:assert detail.high>=p['target']and exit['safe_price']>=p['target']-.101
        if end<clock+pd.Timedelta(minutes=15):entry_bars+=1
        reasons[t['exit_reason']]=reasons.get(t['exit_reason'],0)+1;cash+=t['profit_abs']
    assert abs(cash-stats['final_balance'])<1e-5
    return {'passed':True,'actual_funded_state_rows':len(rows),'source_intents':signals,'held_bar_state_updates_skipped':skips,'source_state_level_max_error':maximum,'actual_entries_audited':len(trades),'entrybar_exits_native_active':entry_bars,'exit_reasons':reasons,'intent_matrix_is_not_portfolio_weights':True}

def audit_account(c,cost,plan,data):
    key=f'{c["name"]}_{cost}bps';path=Path(plan['output_root'])/key;receipt=read(path/'process_receipt.json');assert receipt['returncode']==0 and sha(path/'result.json')==receipt['result_sha256'];verify(plan,c)
    stats,archive,cfg=read_native(path,c['strategy']);frame=pd.read_parquet(path/'hourly_equity.parquet');grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h');assert pd.DatetimeIndex(frame.date).as_unit('ns').equals(grid.as_unit('ns'))
    assert cfg['fee']==cost/10000 and cfg['timeframe']=='15m'and cfg['timeframe_detail']=='5m'and cfg['margin_mode']=='isolated'and stats['dry_run_wallet']==c['capital']
    cash=audit_trades(stats);marked=ledger(stats,frame,c['capital'],data,expected_leverage=3.);policy=state_and_fills(c,path,stats,plan)
    audit={'cash':cash,'marked':marked,'policy':policy,'passed':True,'auditor_sha256':sha(__file__),'archive_sha256':sha(archive),'source_complete_account_replicated':False,'private_settlement':False};save(path/'independent_audit.json',audit)
    nav=frame.set_index('date').hourly_mark_equity;periods=[{'name':p['name'],**metrics(nav.loc[p['start']:p['end']])}for p in plan['periods']]
    native=read(path/'result.json')
    for a,b in zip(periods,native['periods'],strict=True):assert abs(a['net_return']-b['net_return'])<1e-12 and abs(a['maximum_hourly_mark_drawdown']-b['hourly_mark_drawdown'])<1e-12
    return {'account':c['name'],'strategy':c['strategy'],'capital':c['capital'],'cost_bps_per_side':cost,'periods':periods,'trades':len(stats['trades']),'terminal_native_return_separate':stats['profit_total'],'funding_USDT':sum(t['funding_fees']for t in stats['trades']),'audit':audit},nav

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--account');args=parser.parse_args();plan=read(HERE/'plan.json');grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h');data=inputs(plan,grid)
    if args.account:
        candidate=next(c for c in plan['candidates']if args.account.startswith(c['name']+'_'));cost=int(args.account.rsplit('_',1)[1].replace('bps',''));r,_=audit_account(candidate,cost,plan,data);print('AUDITED',args.account,r['trades']);return
    assert read(HERE/'execution_complete.json')['accounts']==12
    accounts=[];navs={}
    for c in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            r,nav=audit_account(c,cost,plan,data);accounts.append(r);navs[f'{c["name"]}_{cost}bps']=nav
    controls_root=ROOT/'research/crypto/freqai_rf_native_20261114_v1/v002';control_plan=read(controls_root/'plan.json');control_result=read(controls_root/'results.json');controls=[]
    for c in control_plan['candidates']:
        if c['strategy']not in ['SmaPriceBTC','HoldBTC']:continue
        verify(control_plan,c)
        for cost in plan['total_cost_bps_per_side']:
            path=Path(control_plan['output_root'])/f'{c["name"]}_{cost}bps';a=read(path/'independent_audit.json');assert a['cash']['passed']and a['marked']['passed']and a['policy']['passed']
            frame=pd.read_parquet(path/'hourly_equity.parquet');navs[f'{c["name"]}_{cost}bps']=frame.set_index('date').hourly_mark_equity
            controls.append(next(r for r in control_result['accounts']if r['account']==c['name']and r['cost_bps_per_side']==cost))
    combos=[]
    for cost in plan['total_cost_bps_per_side']:
        for a,b in [('ProfileBreakout','ProfileFade'),('ProfileBreakout','SmaPriceBTC'),('ProfileFade','SmaPriceBTC'),('ProfileNoVolume','SmaPriceBTC')]:
            nav=combine(navs[f'{a}_500_{cost}bps'],navs[f'{b}_500_{cost}bps']);name=f'{a}_500_plus_{b}_500_{cost}bps';pd.DataFrame({'date':nav.index,'hourly_mark_equity':nav.to_numpy()}).to_parquet(HERE/f'combo_{name}.parquet',index=False)
            combos.append({'name':name,'a':a,'b':b,'cost_bps_per_side':cost,'periods':[{'name':p['name'],**metrics(nav.loc[p['start']:p['end']])}for p in plan['periods']]})
    gates=[]
    for cost in plan['total_cost_bps_per_side']:
        no=next(r for r in accounts if r['strategy']=='ProfileNoVolume'and r['capital']==1000 and r['cost_bps_per_side']==cost)
        for strategy in ['ProfileBreakout','ProfileFade']:
            r=next(r for r in accounts if r['strategy']==strategy and r['capital']==1000 and r['cost_bps_per_side']==cost);nr=read(Path(plan['output_root'])/f'{strategy}_1000_{cost}bps/result.json')
            checks={'positive_full_and_year_slices':all(p['net_return']>0 for p in r['periods']),'DD_le_50pct':r['periods'][0]['maximum_hourly_mark_drawdown']<=.5,'minimum_completed_cycles':nr['periods'][0]['completed_campaigns']>=20 and all(p['completed_campaigns']>=5 for p in nr['periods'][1:])}
            if strategy=='ProfileBreakout':checks['beats_matched_NoVolume']=r['periods'][0]['net_return']>no['periods'][0]['net_return']
            gates.append({'strategy':strategy,'cost':cost,'checks':checks,'passed':all(checks.values())})
    combo_gates=[]
    for combo in combos:
        cost=combo['cost_bps_per_side'];parts=[]
        for strategy in [combo['a'],combo['b']]:
            parts.append(next(a for a in accounts+controls if a['strategy']==strategy and a['capital']==1000 and a['cost_bps_per_side']==cost))
        sma=next(a for a in controls if a['strategy']=='SmaPriceBTC'and a['capital']==1000 and a['cost_bps_per_side']==cost)
        checks={'positive_full_and_year_slices':all(p['net_return']>0 for p in combo['periods']), 'DD_le_50pct':combo['periods'][0]['maximum_hourly_mark_drawdown']<=.5, 'beats_actual1000_components_and_SMA':combo['periods'][0]['net_return']>max(a['periods'][0]['net_return']for a in parts+[sma])}
        combo_gates.append({'name':combo['name'],'a':combo['a'],'b':combo['b'],'cost':cost,'checks':checks,'passed':all(checks.values()),'ablation_control_only':combo['a']=='ProfileNoVolume'})
    result={'complete_new_native_accounts':12,'reused_context_control_accounts':8,'accounts':accounts,'controls':controls,'combinations':combos,'source_gates':gates,'combo_gates':combo_gates,'source_candidate_passes_both_costs':{s:all(g['passed']for g in gates if g['strategy']==s)for s in ['ProfileBreakout','ProfileFade']},'accepted_new_independent_factors':0,'factor_analysis_calls':0,'independent_oos':False,'objective_achieved':False,'private_settlement':False}
    result['incumbent_followup_required']=any(result['source_candidate_passes_both_costs'].values())
    save(HERE/'results.json',result);print(json.dumps({'accounts':12,'gates':gates,'candidate_passes':result['source_candidate_passes_both_costs']},indent=2))
if __name__=='__main__':main()
