"""Audit actual native archives and add only predeclared funded sleeve NAVs."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.crypto.band_trend_native_20261113_v1.analyze import combine,metrics,read_native
from research.crypto.tsmom_risk_native_20261114_v1.analyze import inputs,ledger,policy
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify,audit_trades,sha,save
HERE=Path(__file__).resolve().parent

def campaigns(stats,start,end):
    a,b=pd.Timestamp(start).timestamp()*1000,pd.Timestamp(end).timestamp()*1000
    rows=[t for t in stats['trades']if a<=t['close_timestamp']<b and t['exit_reason']!='force_exit']
    return {'natural_completed_campaigns':len(rows),'winning_campaigns':sum(t['profit_abs']>0 for t in rows),
            'campaign_win_rate':sum(t['profit_abs']>0 for t in rows)/len(rows)if rows else None}

def numeric(parts):
    return all(p['net_return']>0 and p['maximum_hourly_mark_drawdown']<=.30
               and p['natural_completed_campaigns']>=(20 if p['name']=='continuous'else 5)for p in parts)

def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    assert json.loads((HERE/'execution_complete.json').read_text())['accounts']==16
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');data=inputs(plan,grid)
    # Completeness check is specific to BTC's supplied 8-hourly funding grid;
    # private exchange ownership/settlement certification remains separate.
    funding=pd.read_feather(out/'candles/futures/BTC_USDT_USDT-1h-funding_rate.feather')
    observed=pd.DatetimeIndex(funding.loc[funding.date.ge(pd.Timestamp(plan['start']))&funding.date.lt(pd.Timestamp(plan['end'])),'date']).as_unit('ns')
    assert observed.equals(pd.date_range(plan['start'],plan['end'],freq='8h',inclusive='left').as_unit('ns'))
    accounts=[];audits=[];navs={};statistics={}
    for c in plan['candidates']:
        verify(plan,c)
        for cost in plan['total_cost_bps_per_side']:
            key=f'{c["name"]}_{cost}bps';path=out/key
            receipt=json.loads((path/'process_receipt.json').read_text())
            assert receipt['returncode']==0 and receipt['result_sha256']==sha(path/'result.json')
            callback=json.loads((path/'callback_audit.json').read_text());assert callback['passed']and not callback['suppressed_callback_errors']
            stats,archive,cfg=read_native(path,c['strategy'])
            assert cfg['fee']==cost/10000 and cfg['dry_run_wallet']==c['capital']and cfg['margin_mode']=='cross'
            assert cfg['position_adjustment_enable']and stats['trading_mode']=='futures'
            assert all(t['leverage']==3 and not t['is_short']for t in stats['trades'])
            frame=pd.read_parquet(path/'hourly_equity.parquet');nav=frame.set_index('date').hourly_mark_equity
            assert nav.index.as_unit('ns').equals(grid)and nav.iloc[0]==c['capital']
            cash=audit_trades(stats);marked=ledger(stats,frame,c['capital'],data);rules=policy(plan,c,path,stats)
            # Old auditor's economic-admission flag belongs to its29coin TIA
            # study, not this BTC-only model. Retain only applicable diagnostics.
            marked.pop('complete_economic_account_admitted');marked.pop('known_missing_tia_event_unexposed')
            assert not marked.pop('tia_missing_event_actual_exposure')
            marked['complete_BTC_supplied_funding_grid']=True
            parts=[dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]),**campaigns(stats,p['start'],p['end']))for p in plan['periods']]
            old=json.loads((path/'result.json').read_text())['periods']
            for p,q in zip(parts,old,strict=True):
                assert abs(p['net_return']-q['net_return'])<1e-12
                assert abs(p['maximum_hourly_mark_drawdown']-q['hourly_mark_drawdown'])<1e-12
            row={'account':c['name'],'strategy':c['strategy'],'capital':c['capital'],'cost_bps_per_side':cost,
                 'periods':parts,'native_terminal_return_separate':stats['profit_total'],
                 'native_trades_including_forced':len(stats['trades']),'natural_completed_campaigns':parts[0]['natural_completed_campaigns'],
                 'filled_orders':marked['filled_orders'],'funding_net_usdt':old and json.loads((path/'result.json').read_text())['native_funding_net_usdt'],
                 'numeric_gate':numeric(parts)}
            accounts.append(row);navs[key]=nav;statistics[key]=stats
            audit={'account':key,'archive':str(archive),'archive_sha256':sha(archive),'cash':cash,'marked':marked,'policy':rules,'callback':callback}
            audits.append(audit);save(path/'independent_native_audit.json',audit)
            print('AUDITED',key,'net',parts[0]['net_return'],'DD',parts[0]['maximum_hourly_mark_drawdown'],flush=True)
    combos=[];gates=[]
    for cost in plan['total_cost_bps_per_side']:
        full={r['strategy']:r for r in accounts if r['capital']==1000 and r['cost_bps_per_side']==cost}
        best=max(r['periods'][0]['net_return']for r in full.values())
        source=full['SourceGeoHmmBTC'];source_beats_controls=all(source['periods'][0]['net_return']>r['periods'][0]['net_return']for k,r in full.items()if k!='SourceGeoHmmBTC')
        for spec in plan['frozen_combinations']:
            keys=[f'{name}_{cost}bps'for name in spec['actual_wallets']]
            nav=combine(navs[keys[0]],navs[keys[1]])
            nav.to_frame('hourly_mark_equity').to_parquet(HERE/f'combo_{spec["name"]}_{cost}bps.parquet')
            parts=[]
            for p in plan['periods']:
                counts=[campaigns(statistics[k],p['start'],p['end'])for k in keys]
                completed=sum(c['natural_completed_campaigns']for c in counts);wins=sum(c['winning_campaigns']for c in counts)
                parts.append(dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]),natural_completed_campaigns=completed,
                                  winning_campaigns=wins,campaign_win_rate=wins/completed if completed else None))
            left,right=[navs[k].resample('1D').last().pct_change().dropna()for k in keys]
            corr=float(left.corr(right));corr=corr if np.isfinite(corr)else None
            row={'name':spec['name'],'cost_bps_per_side':cost,'actual_wallets':keys,'periods':parts,
                 'daily_sleeve_return_correlation_descriptive':corr,'best_matched_1000_return':best,
                 'beats_best_matched_1000':parts[0]['net_return']>best,
                 'source_combined_1000_return':source['periods'][0]['net_return'],
                 'numeric_gate':numeric(parts)and parts[0]['net_return']>best}
            combos.append(row)
        gates.append({'cost_bps_per_side':cost,'source_beats_matched_controls':source_beats_controls,
                      'source_account_numeric_gate':source['numeric_gate'],
                      'source_joint_gate':source_beats_controls and source['numeric_gate']})
    save(HERE/'native_validation.json',{'passed':True,'accounts':16,'hourly_points_per_account':len(grid),'audits':audits,
         'BTC_supplied_funding_events':len(observed),'actual_private_settlement_certified':False})
    save(HERE/'results.json',{'accounts':accounts,'combinations':combos,'gates':gates,
         'source_passes_both_costs':all(g['source_joint_gate']for g in gates),
         'any_combination_passes_both_costs':any(all(c['numeric_gate']for c in combos if c['name']==name)for name in ['separate_components','source_plus_constant']),
         'accepted_new_factor_count':0,'source_equity_performance_reproduced':False,'independent_oos':False,
         'deployment_eligible':False,'objective_complete':False})
if __name__=='__main__':main()
