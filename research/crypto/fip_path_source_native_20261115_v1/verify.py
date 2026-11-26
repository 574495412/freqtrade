"""Re-read native outputs and frozen predicates, without candidate arithmetic."""
import hashlib
import inspect
import json
import math
from pathlib import Path

import pandas as pd
from bootstrap import HERE, ROOT, ENGINE, boot

native=boot()
from freqtrade.data.dataprovider import DataProvider
from freqtrade.strategy import IStrategy
from FipInputs import FipInputs


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def save(name,value):(HERE/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    protocol=read(HERE/'protocol.json');freeze=read(HERE/'protocol_freeze.json')
    assert sha(freeze['path'])==freeze['sha256']
    assert sha(protocol['parent_pool']['path'])==protocol['parent_pool']['sha256']
    for path,digest in read(HERE/'implementation_freeze.json').items():assert sha(path)==digest,path
    for path,digest in read(HERE/'predictive/owners.json').items():assert sha(path)==digest,path
    for path,digest in read(HERE/'owner_versions.json')['source_files'].items():assert sha(path)==digest,path
    assert {str(p.relative_to(ENGINE))for p in ENGINE.rglob('*.py')}=={p for p in native['files']if p.endswith('.py')}
    assert all('/CZ-Agent/vendor/freqtrade/'in inspect.getfile(cls)for cls in [IStrategy,DataProvider])
    inputs=read(HERE/'input_admission.json');assert inputs['pairs']==protocol['pairs']
    assert len(inputs['native_inputs'])==80 and inputs['protocol_sha256']==sha(HERE/'protocol.json')
    for row in inputs['native_inputs']:assert sha(row['path'])==row['sha256'],row['path']
    for row in read(HERE/'source_receipts.json'):assert sha(row['path'])==row['sha256']
    p=read(HERE/'native_preflight.json')
    assert p['passed']and p['assets']==40 and p['independent_feature_scalars']==44720
    assert p['source_exact_kernel_cells']==287360 and p['maximum_scalar_error']<1e-10
    assert p['actual_indicator_mapping_cells']==14064960
    assert sum(r['actual_feature_cells']for r in p['prefix_checks'])==2437240
    assert all(r['passed']for r in p['prefix_checks'])
    assert p['complete_nonoverlapping_weeks']==86 and len(p['incomplete_tail_decisions'])==1
    results=read(HERE/'predictive_results.json')
    assert results['actual_registry_views']==7 and results['actual_factor_analysis_calls']==21
    assert results['independent_ic_rows_including_reused_splits']==1204
    assert results['independent_group_nav_cells']==12040 and results['maximum_group_nav_error']<1e-10
    counts={'all':86,'calendar2025':53,'calendar2026_through_aug':33}
    by_key={}
    for row in results['receipts']:
        directory=HERE/'predictive'/row['factor']/row['split']/'native'
        summary=read(directory/'ic_summary.json')
        values=pd.read_csv(directory/'ic_series.csv',index_col=0).iloc[:,0].tolist()
        assert len(values)==summary['ic_count']==counts[row['split']]==row['observations']
        mean=math.fsum(values)/len(values)
        sd=math.sqrt(math.fsum((v-mean)**2 for v in values)/(len(values)-1))
        assert abs(mean-summary['ic_mean'])<=.000000500001
        assert abs(sd-summary['ic_std'])<=.000000500001
        assert abs(mean/sd-summary['ir'])<=.0000500001
        assert abs(sum(v>0 for v in values)/len(values)-summary['ic_positive_ratio'])<=.0000500001
        assert all(row['result'][key]==value for key,value in summary.items())
        assert pd.read_csv(directory/'group_equity.csv',index_col=0).shape==(len(values),10)
        gate=summary['ic_mean']>.03 and summary['ir']>.5 and summary['ic_positive_ratio']>.55
        assert row['positive_gate']==gate
        by_key[row['factor'],row['split']]=dict(mean=mean,gate=gate)
    assert len(by_key)==21
    dedup=read(HERE/'predictive/deduplication.json');assert len(dedup['comparisons'])==32
    for path,digest in dedup['parent_bindings'].items():assert sha(path)==digest,path
    for item in results['admissions']:
        name=item['factor'];window=name.removeprefix('signed_path')
        gate=all(by_key[name,split]['gate']for split in counts)
        beat=all(by_key[name,split]['mean']>by_key['pret'+window,split]['mean']for split in counts)
        duplicate=any(r['duplicate_ge_99']for r in dedup['comparisons']if r['factor']==name)
        assert item['all_positive_gates']==gate and item['beats_same_window_pret_all_splits']==beat
        assert item['scoped_duplicate']==duplicate
        assert item['predictive_stage_admitted']==(gate and beat and not duplicate)
        assert not item['predictive_stage_admitted']and not item['financial_stage_admitted']
    assert '14 passed'in (HERE/'tests_initial.log').read_text()
    for log in ['lint_initial.log','semantic_lint_initial.log']:assert (HERE/log).read_text().strip()=='All checks passed!'
    a=FipInputs({});assert not a.confirm_trade_entry()
    for action in [a.ft_bot_start,a.bot_loop_start]:
        try:action()
        except ValueError:pass
        else:raise AssertionError('Diagnostic financial lifecycle admitted')
    unchanged={}
    for study in ['max_lottery_source_native_20261115_v1','source_reversal_adx_native_20261115_v1']:
        manifest=read(HERE.parent/study/'artifact_manifest.json')
        for entry in manifest['files']:assert sha(entry['path'])==entry['sha256'],entry['path']
        unchanged[study]=len(manifest['files'])
    save('verification.json',dict(passed=True,native_commit=native['commit'],native_original_files=595,
        input_files=80,assets=40,source_kernel_cells=287360,independent_feature_scalars=44720,
        indicator_mapping_cells=14064960,feature_prefix_cells=2437240,tests_distinct=14,
        actual_factor_analysis_calls=21,unique_nonoverlapping_weekly_observations=86,
        ic_rows_reusing_splits=1204,group_nav_cells=12040,maximum_group_nav_error=results['maximum_group_nav_error'],
        scoped_peer_comparisons=32,unchanged_prior_crypto_seals=unchanged,
        global_registry_mutated=False,new_accepted_alphas=0,historical_accounts=0,combination_accounts=0,
        bench_records=0,strategy_return=None,higher_net_return_verified=False,independent_oos=False,
        objective_achieved=False,orders_enabled=False,services_changed=False))
    print('VERIFIED frozen inputs/source ownership,21actualFA,1204IC/12040groupcells;2rejections;0fundedaccounts')


if __name__=='__main__':main()
