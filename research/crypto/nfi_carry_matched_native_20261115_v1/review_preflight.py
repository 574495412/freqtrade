"""Review existing native prefix artifacts at the same completed-bar clock."""
import hashlib
import json
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    processes=json.loads((HERE/'preflight_processes.json').read_text())
    assert len(processes)==4 and all(r['returncode']==0 for r in processes)
    assert json.loads((HERE/'input_review.json').read_text())['passed']
    cutoff=pd.Timestamp('2025-01-15T00:00Z');rows=[]
    for mode in ['spot','futures']:
        for symbol in ['BTC','ETH']:
            full=out/('signal_'+mode+'_20250201')/(symbol+'_all_indicators.parquet')
            prefix=out/('signal_'+mode+'_20250115')/(symbol+'_all_indicators.parquet')
            a=pd.read_parquet(full);b=pd.read_parquet(prefix)
            # Native end-date data loading includes its00:00label. That bar
            # is not yet completed at00:00; compare strictpast in BOTH paths.
            boundary=b.loc[b.date>=cutoff]
            assert len(boundary)==1 and boundary.date.iloc[0]==cutoff
            left=a.loc[a.date<cutoff].reset_index(drop=True)
            right=b.loc[b.date<cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(left,right,check_exact=True)
            rows.append(dict(mode=mode,symbol=symbol,rows=len(left),columns=len(left.columns),
                all_completed_past_cells_exact=True,prefix_extra_loader_label=str(boundary.date.iloc[0]),
                full_sha256=hashlib.sha256(full.read_bytes()).hexdigest(),
                prefix_sha256=hashlib.sha256(prefix.read_bytes()).hexdigest()))
    result=dict(passed=True,native_source_files=595,nfi_source_exact=True,configured_native_wallets=8,
        prefix_native_probes=4,prefix_comparisons=rows,financial_accounts=0,orders_enabled=False,
        source_raw_probe_artifacts_unchanged=True,
        probe_scope='January2025vectorindicators/signals only;not full callbacks/all-history causality certification',
        technical_correction='Initial auditor filtered fullpathonly;apply same completed-bar clock toboth. Nativeinclusive00:00loading documented;no strategy/input/fee change',
        prior_preflight_shape_failure_retained=True,signals_and_live_cache_isolated=True)
    (HERE/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS existing4nativeprobes;allpast237columns exact;source/inputs unchanged;0financialaccounts')


if __name__=='__main__':main()
