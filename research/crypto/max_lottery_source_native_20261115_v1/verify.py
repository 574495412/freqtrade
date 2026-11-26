"""Inspect actual outputs and source/input ownership, then seal the failed candidate."""
import hashlib
import inspect
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from bootstrap import HERE, ROOT, ENGINE, boot

native = boot()
from MaxInputs import MaxInputs
from freqtrade.strategy import IStrategy
from freqtrade.data.dataprovider import DataProvider


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(name,value):
    (HERE/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    protocol=read(HERE/'protocol.json');preflight=read(HERE/'native_preflight.json')
    assert protocol['source']['direction'].startswith('positive')
    assert sha(protocol['parent_plan']['path'])==protocol['parent_plan']['sha256']
    assert {str(p.relative_to(ENGINE))for p in ENGINE.rglob('*.py')}=={p for p in native['files']if p.endswith('.py')}
    for path,digest in read(HERE/'implementation_freeze.json').items():assert sha(path)==digest,path
    for path,digest in read(HERE/'predictive/owners.json').items():assert sha(path)==digest,path
    for path,digest in read(HERE/'owner_versions.json')['source_files'].items():assert sha(path)==digest,path
    inputs=read(HERE/'input_admission.json')
    assert inputs['pairs']==protocol['pairs'] and len(inputs['native_inputs'])==80
    assert inputs['protocol_sha256']==sha(HERE/'protocol.json')
    for record in inputs['native_inputs']:assert sha(record['path'])==record['sha256']
    assert sha(HERE/'input_admission_initial.json')==sha(HERE/'input_admission.json')
    assert sha(HERE/'input_config_initial.json')==sha(HERE/'input_config.json')
    assert preflight['passed'] and preflight['independent_feature_scalars']==17200
    assert preflight['actual_indicator_mapping_cells']==5409600 and preflight['assets']==40
    assert preflight['exact_source_prices_and_observation_instants']
    assert preflight['complete_nonoverlapping_weeks']==86
    assert sum(r['actual_feature_cells']for r in preflight['prefix_checks'])==937400
    assert all(r['passed']for r in preflight['prefix_checks'])
    result=read(HERE/'predictive_results.json')
    assert result['actual_factor_analysis_calls']==9 and len(result['receipts'])==9
    assert result['independent_ic_rows_including_reused_splits']==516
    assert result['independent_group_nav_cells']==5160 and result['maximum_group_nav_error']<1e-10
    assert not result['source_factor_admitted_to_financial_stage']
    counts={'all':86,'calendar2025':53,'calendar2026_through_aug':33}
    checked=0
    for row in result['receipts']:
        directory=HERE/'predictive'/row['factor']/row['split']/'native'
        summary=read(directory/'ic_summary.json')
        values=pd.read_csv(directory/'ic_series.csv',index_col=0).iloc[:,0].tolist()
        assert len(values)==summary['ic_count']==counts[row['split']]==row['observations']
        mean=math.fsum(values)/len(values)
        std=math.sqrt(math.fsum((v-mean)**2 for v in values)/(len(values)-1))
        assert abs(mean-summary['ic_mean'])<=.000000500001
        assert abs(std-summary['ic_std'])<=.000000500001
        assert abs(mean/std-summary['ir'])<=.0000500001
        assert abs(sum(v>0 for v in values)/len(values)-summary['ic_positive_ratio'])<=.0000500001
        assert all(row['result'][k]==v for k,v in summary.items())
        assert pd.read_csv(directory/'group_equity.csv',index_col=0).shape==(len(values),10)
        gate=summary['ic_mean']>.03 and summary['ir']>.5 and summary['ic_positive_ratio']>.55
        assert gate==row['positive_gate']
        if row['factor']=='max28':assert not gate and summary['ic_mean']<0
        if row['factor']=='lowvol28':assert gate
        checked+=1
    assert checked==9 and len(result['deduplication'])==10
    assert not any(r['duplicate_gt_99']for r in result['deduplication'])
    dedup=read(HERE/'predictive/deduplication.json')
    for path,digest in dedup['parent_bindings'].items():assert sha(path)==digest
    for name in ['preflight_process_final.json','predictive_process.json']:
        process=read(HERE/name)
        assert process['returncode']==0 and process['pid']>0 and process['free_at_launch']>=2*1024**3
    assert read(HERE/'preflight_process.json')['returncode']==1
    for name in ['source_receipts.json','social_receipts.json']:
        for receipt in read(HERE/name):
            if 'path'in receipt:assert sha(receipt['path'])==receipt['sha256']
    table=read(HERE/'table_receipt.json');assert table['status']==200 and sha(table['path'])==table['sha256']
    source=read(HERE/'source_intake.json')
    assert sha(source['x_twitter']['previous_intake'])==source['x_twitter']['previous_sha256']
    assert source['primary']['source_claims_used_as_local_accounts'] is False
    resources=list(HERE.glob('resource_receipt_*.json'));assert len(resources)==1
    resource=read(resources[0]);assert resource['hardlinked']==135 and resource['resource_floor_lowered']is False
    assert resource['logical_duplicate_bytes']==2157408481
    for row in resource['records']:
        source_path,target=Path(row['canonical']),Path(row['path'])
        assert source_path.stat().st_ino==target.stat().st_ino
        assert sha(source_path)==sha(target)==row['sha256']
    assert '21 passed' in (HERE/'tests_final.log').read_text()
    for name in ['lint_final.log','semantic_lint_final.log']:
        assert (HERE/name).read_text().strip()=='All checks passed!'
    try:
        object.__new__(MaxInputs).ft_bot_start()
    except ValueError as exc:
        assert 'not admitted'in str(exc)
    else:raise AssertionError('Input-only source unexpectedly admits funded lifecycle')
    owners={c.__name__:inspect.getfile(c)for c in [IStrategy,DataProvider]}
    assert all(str(ENGINE)in p for p in owners.values())
    save('verification.json',dict(passed=True,native_commit=native['commit'],native_files=len(native['files']),
        native_runtime_owners=owners,checks=21,input_files=80,assets=40,
        independent_feature_scalars=17200,indicator_mapping_cells=5409600,
        prefix_field_cells_with_overlap=937400,native_registry_views=3,actual_factor_analysis_calls=9,
        unique_nonoverlapping_weeks=86,independent_ic_rows_including_reused_splits=516,
        independent_group_nav_cells=5160,declared_dedup_peers=10,source_direction_preserved=True,
        candidate_predictive_gate_passed=False,lowvol_control_not_promoted_to_new_alpha=True,
        historical_funded_accounts=0,combination_accounts=0,bench_records=0,
        new_global_registry_factors=0,accepted_new_alphas=0,financial_returns=None,
        higher_comparable_return_found=False,independent_oos_certified=False,
        objective_achieved=False,orders_enabled=False,deployment=False,services_changed=False))
    out=Path(preflight['factor_output'])
    paths=list(HERE.rglob('*'))+list(out.rglob('*'))+[ROOT/'freqtrade/data/max_lottery.py']
    paths+=[Path(row['path'])for row in inputs['native_inputs']]
    skip={'artifact_manifest.json','checkpoint_receipt.json','verify.log'}
    files=sorted({p for p in paths if p.is_file()and '__pycache__'not in p.parts and p.name not in skip
                  and not any(part.startswith('.')for part in p.relative_to(ROOT).parts)})
    records=[dict(path=str(p),sha256=sha(p),bytes=p.stat().st_size)for p in files]
    save('artifact_manifest.json',dict(at=datetime.now(UTC).isoformat(),scope='Failed source MAX crypto prediction candidate;0financial accounts',files=records))
    save('checkpoint_receipt.json',dict(verified=True,at=datetime.now(UTC).isoformat(),bound_paths=len(records),
        manifest_sha256=sha(HERE/'artifact_manifest.json'),progress='Actual native source feature/Registry/9factor_analysis calls and source-direction economic rejection',
        objective_achieved=False,new_funded_accounts=0,new_combinations=0,
        higher_comparable_return_found=False,orders_enabled=False,services_changed=False))
    print('VERIFIED',len(records),'paths;9actualFAcalls;MAXrejected;0newfundedaccounts')


if __name__=='__main__':main()
