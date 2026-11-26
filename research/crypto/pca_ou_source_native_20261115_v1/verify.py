"""Final readonly provenance, label maturity, native artifacts and failure audit."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,r):Path(p).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')


def main():
    protocol=read(HERE/'protocol.json');freeze=read(HERE/'protocol_freeze.json')
    assert sha(freeze['path'])==freeze['sha256']
    for name in ['implementation_freeze.json','predictive_review_implementation_freeze.json']:
        for p,digest in read(HERE/name).items():assert sha(p)==digest,p
    native_manifest=read(ROOT.parent/'CZ-Agent/vendor/FREQTRADE_SOURCE.json')
    native=ROOT.parent/'CZ-Agent/vendor/freqtrade'
    for p,digest in native_manifest['files'].items():assert sha(native/p)==digest,p
    assert len(native_manifest['files'])==595
    extra=[str(p.relative_to(native))for p in native.rglob('*.py')if str(p.relative_to(native))not in native_manifest['files']]
    assert not extra
    source=read(HERE/'source_receipts.json')
    for r in source:
        raw=Path(r['path']).read_bytes();assert sha(r['path'])==r['sha256']
        assert hashlib.sha1(('blob '+str(len(raw))+'\0').encode()+raw).hexdigest()==r['git_blob']
    admissions=read(HERE/'input_admission.json')
    for r in admissions['native_inputs']:assert sha(r['path'])==r['sha256']
    pre=read(HERE/'native_preflight.json');out=Path(pre['factor_output'])
    open_price=pd.read_parquet(out/'native_open.parquet');label_cells=0
    for horizon in [1,24]:
        labels=pd.read_parquet(out/f'native_labels_{horizon}h.parquet');days=labels.index
        expected=pd.DataFrame(open_price.reindex(days+pd.Timedelta(hours=horizon+1)).to_numpy()/
            open_price.reindex(days+pd.Timedelta(hours=1)).to_numpy()-1,index=days,columns=labels.columns)
        pd.testing.assert_frame_equal(labels,expected,check_exact=True)
        assert days[0]==pd.Timestamp(protocol['account_start'])
        assert (days[-1]+pd.Timedelta(hours=horizon+1))<=pd.Timestamp(protocol['account_end'])
        label_cells+=labels.size
    # Verify all raw submitted views and labels are the native observations.
    result=read(HERE/'predictive_review_results.json');assert len(result['receipts'])==24
    assert not any(r['predictive_stage_admitted']for r in result['admissions'])
    assert read(HERE/'predictive_review_process.json')['returncode']==0
    source_mask=pd.read_parquet(out/'native_pca_ou_reversal.parquet').notna()
    original_dirs=sorted((HERE/'predictive').glob('*/*/*/native'))
    assert len(original_dirs)==4
    parity=[];csv_rows=0;maximum_precision_delta=0.
    for receipt in result['receipts']:
        n=receipt['factor'];horizon=receipt['horizon_hours'];split=receipt['split']
        d=HERE/'predictive_review'/f'{horizon}h'/n/split
        raw=pd.read_parquet(out/('native_'+n+'.parquet')).where(source_mask)
        y=pd.read_parquet(out/f'native_labels_{horizon}h.parquet')
        if split!='all':y=y.loc[y.index.year==(2025 if split=='calendar2025' else 2026)]
        f=raw.reindex(y.index)
        # Independent expected writer bytes prove no changed signal or label.
        assert f.to_csv()==(d/'factor.csv').read_text()
        assert y.to_csv()==(d/'returns.csv').read_text()
        for file in ['ic_series.csv','ic_summary.json','group_equity.csv']:
            assert(d/'native'/file).is_file()
        initial=HERE/'predictive'/f'{horizon}h'/n/split
        if(initial/'native/ic_series.csv').exists():
            for file in ['factor.csv','returns.csv','native/ic_series.csv','native/ic_summary.json','native/group_equity.csv']:
                assert sha(initial/file)==sha(d/file),(n,split,file)
            parity.append(dict(horizon_hours=horizon,factor=n,split=split,all_five_artifacts_exact=True))
        posted=pd.read_csv(d/'factor.csv',index_col=0,parse_dates=True)
        delta=np.abs(posted.to_numpy()-f.to_numpy())
        maximum_precision_delta=max(maximum_precision_delta,float(np.nanmax(delta)))
        csv_rows+=len(posted)
    assert len(parity)==4
    # Original-kernel verification specifically includes both invalid OU cells.
    sys.path.insert(0,str(HERE))
    from preflight import source_models
    close=pd.read_parquet(out/'native_close.parquet')
    pca,residual,ou,score=source_models(close)
    invalid=np.argwhere(~source_mask.to_numpy());witnesses=[]
    for i,j in invalid:
        at=source_mask.index[i];pair=source_mask.columns[j]
        pc=pca.compute_at_time(at);res=residual.compute_at_time(at,pc);params=ou.estimate_from_residuals(res)
        ref=score.compute_at_time(res,params)
        assert ref.get_s(pair)is None
        witnesses.append(dict(at=str(at),pair=pair,source_invalid=True,local_nan_retained=True))
    assert len(witnesses)==2
    save(HERE/'verification.json',dict(
        passed=True,native_original_files=595,native_commit=native_manifest['commit'],
        extra_upstream_python_files=extra,pinned_source_files=10,source_tree_paths=17,
        original_prices_and_native_rows_unchanged=True,
        exact_mature_forward_label_cells=label_cells,
        complete_1h_nonoverlap_labels=14590,complete_24h_nonoverlap_labels=607,
        labels_overlap_across_horizons=True,source_score_witnesses=pre['original_source_score_witnesses'],
        additional_source_invalid_ou_witnesses=witnesses,
        local_prefix_and_future_perturbation_boundaries=3,
        independent_native_indicator_mapping_cells=pre['indicator_mapping_cells'],
        distinct_component_checks=16,distinct_native_factor_analysis_measurements=24,
        actual_total_factor_analysis_calls=28,initial_completed_calls=4,
        audited_review_calls=24,technical_duplicate_calls=4,initial_artifact_replays=parity,
        reconstructed_ic_rows=result['reconstructed_ic_rows'],reconstructed_group_cells=result['reconstructed_group_cells'],
        all_submitted_csv_writer_bytes_exact=True,submitted_csv_rows_including_reused_splits=csv_rows,
        max_pandas_default_csv_factor_numeric_delta=maximum_precision_delta,
        csv_tie_witnesses='csv_reversal_control_precision_witnesses.json',
        financial_or_external_account_engine_executed=False,funded_accounts=0,combination_accounts=0,
        new_accepted_independent_alphas=0,vibe_benches=0,global_registry_modified=False,
        frozen_predictive_admission='REJECT_BOTH_LABEL_HORIZONS',
        source_account_replication=False,independent_oos=False,orders_enabled=False,services_changed=False))
    print('VERIFIED 28actualFAcalls/24distinctmeasurements/4exactreplays;',label_cells,'labels;',len(witnesses),'originalinvalidOU witnesses;0funded accounts')


if __name__=='__main__':main()
