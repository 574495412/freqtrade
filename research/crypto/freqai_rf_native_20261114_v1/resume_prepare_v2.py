"""Resume input-only v2 preparation after the missing bootstrap import failure."""
import json

import pandas as pd

import prepare
from bootstrap import boot
from prepare import HERE,OUT,ref,save,sha

V2=HERE/'v002';NEW=OUT/'v002'


def main():
    boot()
    admission=json.loads((V2/'input_prefix_audit.json').read_text());assert admission['passed']
    for name,digest in json.loads((NEW/'data_receipts.json').read_text()).items():assert sha(NEW/'candles'/name)==digest
    assert not (V2/'plan.json').exists()
    prepare.OUT=NEW;checks=[];start=pd.Timestamp('2025-01-01T00:00Z');cutoff=start+pd.Timedelta(days=3)
    for strategy in ['RollingRF','RollingPrior']:
        original,record=prepare.native_component(strategy,start,'complete_original');assert record['training_rows']==700
        def changed(frame):
            f=frame.copy();f.loc[f.date.ge(cutoff),['open','high','low','close','volume']]*=11;return f
        for variant,modify in [('complete_future',changed),('complete_truncate',lambda f:f.loc[f.date.lt(cutoff)].copy())]:
            actual,test=prepare.native_component(strategy,start,variant,modify);assert test['training_rows']==700
            columns=['date','&-direction','up','down','do_predict','target_weight','source_signal']
            pd.testing.assert_frame_equal(original.loc[original.date.lt(cutoff),columns].reset_index(drop=True),actual.loc[actual.date.lt(cutoff),columns].reset_index(drop=True),check_exact=True)
        checks.append(dict(strategy=strategy,actual_component_fits=3,first_training_rows=700,prefix_checks_passed=True))
    plan=json.loads((HERE/'plan.json').read_text())
    plan.update(schema='freqtrade.native.freqai-rf.data-v2',output_root=str(NEW),datadir=str(NEW/'candles'),data_receipts=ref(NEW/'data_receipts.json'))
    plan['marks']={'BTC/USDT:USDT':ref(NEW/'candles/futures/BTC_USDT_USDT-1h-mark.feather')}
    plan['additional_source_seals'] += [ref(V2/'data_contract_amendment.json'),ref(V2/'input_prefix_audit.json')]
    save(V2/'plan.json',plan);save(V2/'preflight.json',dict(passed=True,checks=checks,additional_native_component_fits=6,
         requested_training_warmup_complete=True,initial_bootstrap_failure_retained=True,financial_accounts=0))
    source=(HERE/'run.py').read_text().replace("plan_path=HERE/'plan.json'","plan_path=HERE/'v002/plan.json'")
    source=source.replace("HERE/'execution_complete.json'","HERE/'v002/execution_complete.json'")
    (HERE/'run_v2.py').write_text(source)
    save(V2/'runtime_source_freeze.json',{str(p):sha(p) for p in HERE.glob('*.py')})
    print('Corrected first training windows contain700mature rows; both native prefix checks pass')


if __name__=='__main__':main()
