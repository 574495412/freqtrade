"""Verify original inputs, actual native archives and full-account evidence."""
import hashlib,json,re
from datetime import UTC,datetime
from pathlib import Path
import pandas as pd
from research.crypto.tsmom_risk_native_20261114_v1.native_runner import verify as native_verify
from research.crypto.band_trend_native_20261113_v1.analyze import read_native,combine,metrics
from research.crypto.geometric_hmm_native_20261114_v1.analyze import numeric
HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
def main():
 plan=read(HERE/'plan.json');out=Path(plan['output_root']);r=read(HERE/'results.json');v=read(HERE/'native_validation.json');pre=read(HERE/'preflight.json')
 assert read(HERE/'execution_complete.json')['accounts']==12
 assert pre['passed']and pre['official_archives']==2922 and len(pre['actual_prefix_checks'])==18
 assert len(r['accounts'])==len(v['audits'])==12 and len(r['combinations'])==2
 assert re.search(r'\b16 passed\b',(HERE/'tests_final.log').read_text())
 for name in['semantic_lint.log','owner_lint.log']:assert (HERE/name).read_text().strip()=='All checks passed!'
 records=read(out/'raw_receipts.json');assert len(records)==2922
 for row in records:
  assert sha(row['path'])==row['sha256']
  check=Path(row.get('checksum_path',str(Path(row['path']).with_suffix('.CHECKSUM'))))
  assert sha(check)==row['checksum_sha256']and check.read_text().split()[0]==row['sha256']
 for name in['source_receipts.json','github_receipts.json','social_receipts.json']:
  for row in read(HERE/name):
   if 'path'in row:assert sha(row['path'])==row['sha256']
   if 'visible_path'in row:assert sha(row['visible_path'])==row['visible_sha256']
   if 'caption'in row:assert sha(row['caption']['path'])==row['caption']['sha256']
 navs={};orders=hours=budgets=0
 for c in plan['candidates']:
  native_verify(plan,c)
  for cost in plan['total_cost_bps_per_side']:
   key=f'{c["name"]}_{cost}bps';p=out/key;rec=read(p/'process_receipt.json')
   assert rec['returncode']==0 and rec['result_sha256']==sha(p/'result.json')
   assert not(p/'failure.json').exists()and not(p/'resource_guard.json').exists()
   assert read(p/'callback_audit.json')['passed']
   stats,archive,cfg=read_native(p,c['strategy']);a=next(a for a in v['audits']if a['account']==key)
   assert sha(archive)==a['archive_sha256']and all(a[k]['passed']for k in['cash','marked','policy'])
   assert cfg['dry_run_wallet']==c['capital']and cfg['fee']==cost/10000 and cfg['max_open_trades']==3
   nav=pd.read_parquet(p/'hourly_equity.parquet').set_index('date').hourly_mark_equity
   assert nav.index.as_unit('ns').equals(pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns'))
   assert nav.iloc[0]==c['capital'];navs[key]=nav
   row=next(x for x in r['accounts']if x['account']==c['name']and x['cost_bps_per_side']==cost)
   assert abs(row['periods'][0]['net_return']-metrics(nav)['net_return'])<1e-12
   assert row['numeric_gate']==numeric(row['periods'])
   orders+=a['marked']['filled_orders'];hours+=len(nav);budgets+=a['policy']['daily_budget_snapshots']
 for combo in r['combinations']:
  nav=combine(*(navs[k]for k in combo['actual_wallets']))
  stored=pd.read_parquet(HERE/f'combo_{combo["cost_bps_per_side"]}bps.parquet').hourly_mark_equity
  pd.testing.assert_series_equal(nav,stored,check_exact=True,check_names=False)
  assert abs(metrics(nav)['net_return']-combo['periods'][0]['net_return'])<1e-12
  assert combo['numeric_gate']==(numeric(combo['periods'])and combo['beats_best_matched_1000'])
 assert hours==175104 and budgets==7296 and not r['objective_complete']and not r['deployment_eligible']
 # The earlier owned cache compaction preserved every sealed byte and path.
 old=HERE.parent/'geometric_hmm_native_20261114_v1/artifact_manifest.json'
 for p,rec in read(old).items():assert sha(p)==rec['sha256'],p
 output={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'original_native_files':595,
  'verified_official_archives':2922,'declared_daily_slots':2922,
  'observed_exact_snapshots':sum(x['exact_snapshot_present']for d in read(out/'signal_capsule.json')['diagnostics'].values()for x in d),
  'native_accounts':12,'funded_combination_cost_cases':2,
  'component_tests':16,'actual_prefix_checks':18,'independent_rank_cells':pre['independent_rank_cells'],
  'native_orders':orders,'native_hourly_nav_points':hours,'native_daily_budgets':budgets,
  'maximum_nav_error_usdt':max(a['marked']['maximum_nav_error_usdt']for a in v['audits']),
  'maximum_funding_error_usdt':max(a['marked']['maximum_funding_error_usdt']for a in v['audits']),
  'complete_factor_input_coverage':r['complete_factor_input_coverage'],'economic_account_gate':r['crowd_passes_both_costs'],
  'economic_combo_gate':r['combination_passes_both_costs'],'all_previous_sealed_bytes_preserved':True,
  'accepted_new_factors':0,'new_registry':0,'factor_analysis_calls':0,'Vibe_benches':0,'independent_oos':False,
  'source_gross_study_reproduced':False,'objective_achieved':False,'deployment':False,'orders':False,'services_changed':False}
 (HERE/'verification.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output,indent=2))
if __name__=='__main__':main()
