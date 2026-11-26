"""Verify actual native input and factor evidence, including unknown dates."""
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
from freqtrade.data.signed_jump import daily_signed_variation
from freqtrade.strategy import IStrategy

HERE=Path(__file__).resolve().parent
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

amendment=read(HERE/'entry_guard_amendment.json')
frozen=read(HERE/'implementation_freeze.json')
assert frozen[amendment['owner']]==amendment['original_frozen_sha256']
assert sha(amendment['preserved_source'])==amendment['original_frozen_sha256']
assert Path(amendment['owner']).read_text().replace(amendment['only_inserted_block'],'')==Path(amendment['preserved_source']).read_text()
frozen[amendment['owner']]=amendment['new_sha256']
for path,h in frozen.items():assert sha(path)==h,path
a=read(HERE/'input_admission.json');assert len(a['archive_receipts'])==168
minute_rows=samples=zeros=0
for r in a['archive_receipts']:
    assert sha(r['archive'])==r['archive_sha256'],r['archive']
    assert sha(r['checksum'])==r['checksum_sha256']
    assert Path(r['checksum']).read_text().split()[0]==r['archive_sha256']
    assert sha(r['sample_path'])==r['sample_sha256']
    assert sha(r['parent_native5m'])==r['parent_native5m_sha256']
    assert r['native5m_close_differences']==0
    minute_rows+=r['minute_rows'];samples+=r['sample_rows'];zeros+=r['zero_volume_samples']
assert minute_rows==7361280 and samples==1472256 and zeros==64
for r in a['minute_vwap_inputs']:
    assert sha(r['path'])==r['sha256'] and r['rows']==184032 and r['unknown_samples']==8
pre=read(HERE/'native_preflight.json')
assert pre['passed_for_input_diagnostics']and not pre['full_financial_input_admitted']
assert pre['daily_decisions']==608 and pre['known_joint_decisions']==606
assert len(pre['unknown_joint_decisions'])==2 and len(pre['unknown_asset_decisions'])==16
assert pre['independent_scalar_moment_cells']==40704 and pre['native_indicator_cells']==613440
assert max(pre['maximum_scalar_errors'].values())<1e-8
assert len(pre['actual_prefix_checks'])==24 and all(r['truncation_and_perturbation']for r in pre['actual_prefix_checks'])
log=(HERE/'tests_guard_final.log').read_text();assert re.search(r'\b25 passed\b',log)and not re.search(r'\bFAILED\b|\bERROR\b',log)
for name in ['lint_final.log','semantic_lint_final.log']:assert (HERE/name).read_text().strip()=='All checks passed!'
for row in read(HERE/'source_receipts.json')+[read(HERE/'paper_followup_receipt.json')]:
    assert sha(row['path'])==row['sha256']
paper=read(HERE/'paper_followup_receipt.json');assert paper['pdf_magic']and Path(paper['path']).read_bytes().startswith(b'%PDF')
visual=read(HERE/'formula_visual_review.json');assert sha(visual['pdf'])==visual['sha256']
for r in visual['pages']:assert sha(r['image'])==r['sha256']
intake=read(HERE/'source_intake.json');old=intake['previous_social_intake_binding'];assert sha(old['path'])==old['sha256']
results=read(HERE/'predictive_results.json');assert results['actual_factor_analysis_calls']==15
assert len(results['receipts'])==15 and all(not r['positive_gate']for r in results['receipts'])
assert all(not r['financial_stage_admitted']for r in results['candidate_status'])
mask=read(HERE/'predictive/mask_receipt.json');assert mask['complete_label_dates']==607 and mask['common_observed_input_dates']==605
ic=groups=0
for r in results['receipts']:
    p=HERE/'predictive'/r['factor']/r['split'];s=read(p/'native/ic_summary.json')
    assert all(s[k]==r['result'][k]for k in s),str(p)
    f=pd.read_csv(p/'factor.csv',index_col=0);y=pd.read_csv(p/'returns.csv',index_col=0)
    assert f.shape==y.shape==(r['observations'],8)and f.index.equals(y.index)and f.columns.equals(y.columns)
    assert f.notna().all().all()and y.notna().all().all()
    curve=pd.read_csv(p/'native/group_equity.csv',index_col=0);observed=pd.read_csv(p/'native/ic_series.csv',index_col=0)
    assert curve.shape==(r['observations'],5)and len(observed)==r['observations']
    ic+=len(observed);groups+=curve.size
assert ic==6050 and groups==30250
for path,h in read(HERE/'predictive/owners.json').items():assert sha(path)==h,path
dedup=read(HERE/'predictive/deduplication.json');assert len(dedup['comparisons'])==24
for path,h in dedup['bindings'].items():assert sha(path)==h,path
assert not any(r['duplicate_gt_99']for r in dedup['comparisons'])
owners={c.__name__:inspect.getfile(c)for c in [DataProvider,IStrategy]}
assert all('/CZ-Agent/vendor/freqtrade/'in p for p in owners.values())
result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_commit':manifest['commit'],
        'native_original_files_verified':len(manifest['files']),'runtime_owners':owners,
        'local_feature_owner':inspect.getfile(daily_signed_variation),'unique_component_checks':25,
        'official_archive_checksums':168,'source_minute_rows':minute_rows,'sampled5m_rows':samples,
        'zero_volume_samples_preserved_unknown':zeros,'known_joint_financial_input_days':606,'original_window_days':608,
        'complete_gross_label_dates':607,'known_input_diagnostic_dates':605,
        'actual_factor_analysis_calls':15,'independent_ic_rows_including_full_year_overlap':ic,'group_nav_cells':groups,
        'source_family_count':1,'new_accepted_alphas':0,'native_funded_accounts':0,'new_combinations':0,'new_bench_records':0,
        'net_account_returns':None,'objective_achieved':False,'orders':False,'deployment':False,'services_changed':False,
        'commit':False,'push':False}
(HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
