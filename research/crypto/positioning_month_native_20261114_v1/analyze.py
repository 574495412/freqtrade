"""Independent audits of native fills and predeclared actual funded combination."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.crypto.band_trend_native_20261113_v1.analyze import read_native,metrics,combine
from research.crypto.tsmom_risk_native_20261114_v1.analyze import inputs,ledger,policy
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify,audit_trades,save,sha
from research.crypto.geometric_hmm_native_20261114_v1.analyze import campaigns,numeric
HERE=Path(__file__).resolve().parent

def main():
 plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
 assert json.loads((HERE/'execution_complete.json').read_text())['accounts']==12
 grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');data=inputs(plan,grid)
 for pair in plan['pairs']:
  f=pd.read_feather(out/'candles/futures'/f'{pair.split("/")[0]}_USDT_USDT-1h-funding_rate.feather')
  clock=pd.DatetimeIndex(f.loc[f.date.ge(pd.Timestamp(plan['start']))&f.date.lt(pd.Timestamp(plan['end'])),'date']).as_unit('ns')
  assert clock.equals(pd.date_range(plan['start'],plan['end'],freq='8h',inclusive='left').as_unit('ns'))
 navs={};statistics={};rows=[];audits=[]
 for c in plan['candidates']:
  verify(plan,c)
  for cost in plan['total_cost_bps_per_side']:
   key=f'{c["name"]}_{cost}bps';path=out/key;r=json.loads((path/'process_receipt.json').read_text())
   assert r['returncode']==0 and r['result_sha256']==sha(path/'result.json')
   callback=json.loads((path/'callback_audit.json').read_text());assert callback['passed']
   stats,archive,cfg=read_native(path,c['strategy'])
   assert cfg['dry_run_wallet']==c['capital']and cfg['fee']==cost/10000 and cfg['margin_mode']=='cross'
   assert cfg['position_adjustment_enable']and cfg['max_open_trades']==3
   assert all(not t['is_short']and t['leverage']==3 for t in stats['trades'])
   frame=pd.read_parquet(path/'hourly_equity.parquet');nav=frame.set_index('date').hourly_mark_equity
   assert nav.index.as_unit('ns').equals(grid)and nav.iloc[0]==c['capital']
   cash=audit_trades(stats);marked=ledger(stats,frame,c['capital'],data);rules=policy(plan,c,path,stats)
   marked.pop('complete_economic_account_admitted');marked.pop('known_missing_tia_event_unexposed')
   assert not marked.pop('tia_missing_event_actual_exposure')
   marked['complete_three_coin_supplied_funding_grid']=True
   parts=[dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]),**campaigns(stats,p['start'],p['end']))for p in plan['periods']]
   native=json.loads((path/'result.json').read_text())
   for p,q in zip(parts,native['periods'],strict=True):
    assert abs(p['net_return']-q['net_return'])<1e-12
    assert abs(p['maximum_hourly_mark_drawdown']-q['hourly_mark_drawdown'])<1e-12
   row={'account':c['name'],'strategy':c['strategy'],'capital':c['capital'],'cost_bps_per_side':cost,'periods':parts,
    'native_terminal_return_separate':stats['profit_total'],'native_trades_including_forced':len(stats['trades']),
    'filled_orders':marked['filled_orders'],'funding_net_usdt':native['native_funding_net_usdt'],'numeric_gate':numeric(parts)}
   rows.append(row);navs[key]=nav;statistics[key]=stats
   a={'account':key,'archive':str(archive),'archive_sha256':sha(archive),'cash':cash,'marked':marked,'policy':rules,'callback':callback}
   audits.append(a);save(path/'independent_native_audit.json',a)
   print('AUDITED',key,'net',parts[0]['net_return'],'DD',parts[0]['maximum_hourly_mark_drawdown'],flush=True)
 combos=[];gates=[]
 for cost in plan['total_cost_bps_per_side']:
  full={r['strategy']:r for r in rows if r['capital']==1000 and r['cost_bps_per_side']==cost}
  main=full['LowCrowdMonth'];best=max(r['periods'][0]['net_return']for r in full.values())
  spec=plan['frozen_combinations'][0];keys=[f'{n}_{cost}bps'for n in spec['actual_wallets']]
  nav=combine(*(navs[k]for k in keys));nav.to_frame('hourly_mark_equity').to_parquet(HERE/f'combo_{cost}bps.parquet')
  parts=[]
  for p in plan['periods']:
   cases=[campaigns(statistics[k],p['start'],p['end'])for k in keys]
   completed=sum(x['natural_completed_campaigns']for x in cases);wins=sum(x['winning_campaigns']for x in cases)
   parts.append(dict(name=p['name'],**metrics(nav.loc[p['start']:p['end']]),natural_completed_campaigns=completed,
    winning_campaigns=wins,campaign_win_rate=wins/completed if completed else None))
  daily=[navs[k].resample('1D').last().pct_change().dropna()for k in keys];cor=float(daily[0].corr(daily[1]))
  combo={'name':spec['name'],'cost_bps_per_side':cost,'actual_wallets':keys,'periods':parts,
    'best_matched_1000_return':best,'beats_best_matched_1000':parts[0]['net_return']>best,
    'daily_sleeve_return_correlation_descriptive':cor if np.isfinite(cor)else None,
    'numeric_gate':numeric(parts)and parts[0]['net_return']>best}
  combos.append(combo)
  beats=all(main['periods'][0]['net_return']>r['periods'][0]['net_return']for k,r in full.items()if k!='LowCrowdMonth')
  gates.append({'cost_bps_per_side':cost,'crowd_beats_matched_controls':beats,'crowd_numeric_gate':main['numeric_gate'],'crowd_joint_gate':beats and main['numeric_gate']})
 pre=json.loads((HERE/'preflight.json').read_text())
 unknown=sum(sum(r['unknown_rank_decisions'].values())for r in pre['native_strategies']if r['strategy']!='ConstantPositioningControl')
 save(HERE/'native_validation.json',{'passed':True,'accounts':12,'hourly_points_per_account':len(grid),'audits':audits,
  'three_coin_supplied_funding_events':1824*3,'actual_private_settlement_certified':False})
 save(HERE/'results.json',{'accounts':rows,'combinations':combos,'gates':gates,'actual_unknown_rank_decisions':unknown,
  'complete_factor_input_coverage':unknown==0,'crowd_passes_both_costs':all(g['crowd_joint_gate']for g in gates),
  'combination_passes_both_costs':all(c['numeric_gate']for c in combos),'new_accepted_factors':0,'independent_oos':False,
  'source_gross_study_reproduced':False,'deployment_eligible':False,'objective_complete':False})
if __name__=='__main__':main()
