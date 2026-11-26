"""Actual source frames: receipt projection matches native missing-column semantics."""
import importlib.util
import json
from pathlib import Path

import pandas as pd

HERE=Path(__file__).absolute().parent


def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    plan=json.loads((HERE/'plan.json').read_text());root=Path(plan['output_root'])/'preflight_reversion_1000_conservative_admission1';old,new=load('CaptureRules'),load('CaptureRulesV2');rows=[]
    for asset in ['BTC','ETH']:
        frame=pd.read_parquet(root/(asset+'_native_signals.parquet'));untouched=frame.copy(deep=True)
        actual=new.signal_receipt(frame,'SimpleMeanReversion');pd.testing.assert_frame_equal(frame,untouched)
        expected=frame.copy()
        for field in ['enter_long','enter_short','exit_long','exit_short']:
            if field not in expected:expected[field]=0
        reference=old.signal_receipt(expected,'SimpleMeanReversion');assert actual==reference
        try:new.signal_receipt(frame.drop(columns='open'),'SimpleMeanReversion')
        except AssertionError:pass
        else:raise AssertionError('Missinginputpricecannotbenormalized')
        rows.append(dict(asset=asset,rows=actual['rows'],sha256=actual['sha256'],missing_optional_flags_match_native_zeros=True,original_frame_unchanged=True,missing_price_rejected=True))
    (HERE/'capture_validation.json').write_text(json.dumps(dict(passed=True,frames=rows,candidate_code_changed=False,economic_runs=0),indent=2)+'\n');print('PASSrealframes;receiptprojectiononly;sourceunchanged')


if __name__=='__main__':main()
