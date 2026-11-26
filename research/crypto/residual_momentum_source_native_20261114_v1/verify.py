"""Verify native factor-stage evidence; no backtest or profit by registration."""
import hashlib
import inspect
import json
import re
from datetime import UTC,datetime
from pathlib import Path

import pandas as pd
from bootstrap import boot

manifest=boot()
from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.residual_momentum import source_residual_momentum
from freqtrade.strategy import IStrategy

HERE=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())

for path,h in read(HERE/'implementation_freeze.json').items():assert sha(path)==h,path
for r in read(HERE/'source_manifest.json')['source_files']:assert sha(r['path'])==r['sha256']
a=read(HERE/'input_admission.json')
for r in a['native_inputs']:
    assert sha(r['parent'])==r['parent_sha256'] and sha(r['path'])==r['sha256']
    assert r['rows']==16056 and r['old_rows_unchanged']
for r in a['archive_receipts']:
    assert sha(r['archive'])==r['archive_sha256']
    assert sha(r['checksum'])==r['checksum_sha256']
    assert Path(r['checksum']).read_text().split()[0]==r['archive_sha256']
pre=read(HERE/'native_preflight.json');assert pre['passed']
assert pre['source_math_finite_cells_exact']==366384 and pre['native_indicator_cells']==256896
assert pre['maximum_scalar_score_error']<1e-10 and pre['independent_scalar_score_cells']==24
assert len(pre['prefix_checks'])==3 and all(x['prefix_and_perturbation_passed']for x in pre['prefix_checks'])
log=(HERE/'tests_final.log').read_text();assert re.search(r'\b18 passed\b',log)and not re.search(r'\bFAILED\b|\bERROR\b',log)
for name in ['lint_final.log','semantic_lint_final.log']:
    assert (HERE/name).read_text().strip()=='All checks passed!'
results=read(HERE/'predictive_results.json')
assert results['actual_factor_analysis_calls']==9 and len(results['receipts'])==9
assert not results['source_factor_admitted_to_financial_stage']
assert not any(r['positive_gate']for r in results['receipts'])
ic=groups=0
for r in results['receipts']:
    p=HERE/'predictive'/r['factor']/r['split']
    summary=read(p/'native/ic_summary.json')
    assert all(summary[k]==r['result'][k]for k in summary),str(p)
    f=pd.read_csv(p/'factor.csv',index_col=0);y=pd.read_csv(p/'returns.csv',index_col=0)
    assert f.shape==y.shape==(r['observations'],8) and f.index.equals(y.index)
    assert f.columns.equals(y.columns) and f.notna().all().all()and y.notna().all().all()
    rows=pd.read_csv(p/'native/ic_series.csv');g=pd.read_csv(p/'native/group_equity.csv',index_col=0)
    assert len(rows)==r['observations'] and g.shape==(r['observations'],4)
    ic+=len(rows);groups+=g.size
assert ic==516 and groups==2064
for path,h in read(HERE/'predictive/owners.json').items():assert sha(path)==h,path
dedup=read(HERE/'predictive/deduplication.json');old=dedup['old_feature_binding']
assert sha(old['path'])==old['sha256']
assert len(dedup['comparisons'])==5 and all(r['observations']==86 for r in dedup['comparisons'])
for r in read(HERE/'social_receipts.json'):
    if 'path'in r:assert sha(r['path'])==r['sha256']
for r in [read(HERE/'social/x_link_receipt.json'),read(HERE/'social/caption_receipt.json'),*read(HERE/'archive_followup_receipts.json')['records']]:
    assert sha(r['path'])==r['sha256']
assert read(HERE/'social/caption_receipt.json')['bytes']==0
runtime={c.__name__:inspect.getfile(c)for c in [DataProvider,IStrategy]}
assert all('/CZ-Agent/vendor/freqtrade/'in p for p in runtime.values())
report={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_commit':manifest['commit'],
        'native_original_files_verified':len(manifest['files']),'runtime_owners':runtime,
        'local_feature_owner':inspect.getfile(source_residual_momentum),'component_checks':18,
        'source_universe':a['source_universe_from_notebook_output'],'official_archives_with_checksums':8,
        'native_rows_per_asset':16056,'original_rows_per_asset_numerically_unchanged':15336,
        'actual_factor_analysis_calls':9,'unique_nonoverlapping_labels':86,
        'ic_rows_including_overlap_between_full_and_year_splits':ic,'group_nav_cells':groups,
        'accepted_new_alphas':0,'native_funded_accounts':0,'new_strategy_combinations':0,
        'new_bench_records':0,'net_account_returns':None,'objective_achieved':False,
        'orders':False,'deployment':False,'services_changed':False,'commit':False,'push':False}
(HERE/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
