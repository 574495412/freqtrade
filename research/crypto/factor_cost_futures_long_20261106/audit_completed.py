"""Incremental read-only audit of completed long accounts; never starts a run."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
SOURCE=HERE.parent/'factor_cost_futures_20261106'
sys.path.insert(0,str(SOURCE))
spec=importlib.util.spec_from_file_location('frozen_futures_account_audit',SOURCE/'score_accounts.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
audit.HERE=HERE

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-id');args=parser.parse_args()
    plan=json.loads((HERE/'plan.json').read_text())
    for name,digest in plan['input_hashes'].items():
        if audit.metrics.sha(ROOT/name)!=digest:raise ValueError('Registered source/data changed: '+name)
    (HERE/'audits').mkdir(exist_ok=True)
    source=json.loads((ROOT/'research/crypto/rd_program_20261105/plan.json').read_text())
    for row in plan['runs']:
        if args.run_id and row['id']!=args.run_id:continue
        output=ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'/row['id']
        if not (output/'result.json').exists():
            print('PENDING',row['id']);continue
        target=HERE/'audits'/(row['id']+'.json')
        if target.exists():
            saved=json.loads(target.read_text())
            if saved['result_sha256']!=audit.metrics.sha(output/'result.json'):raise ValueError('Completed result changed')
            print('ALREADY_AUDITED',row['id']);continue
        result=audit.score(row,plan)
        curve=pd.read_parquet(output/'model_equity.parquet').set_index('date')
        slices=[]
        for fold in source['walk_forward']['folds']:
            start,end=[pd.Timestamp(v,tz='UTC') for v in fold['validation']]
            observed_end=min(end,curve.index[-1]);part=curve.equity.loc[start:observed_end]
            if part.index[0]!=start or part.index[-1]!=observed_end:raise ValueError('Missing slice boundary')
            slices.append(dict(id=fold['id'],start=str(start),requested_end=str(end),observed_end=str(observed_end),
                endpoint_complete=observed_end==end,net_return=float(part.iloc[-1]/part.iloc[0]-1),
                mdd=float((1-part/part.cummax()).max()),reset_account=False,independent_oos=False,
                inherited_positions_and_risk_state=True))
        result.update(result_sha256=audit.metrics.sha(output/'result.json'),continuous_development_slices=slices,
            warning_first_at=str(curve.index[curve.warning][0]) if curve.warning.any() else None,
            hard_halt_first_at=str(curve.index[curve.hard_halt][0]) if curve.hard_halt.any() else None,
            audit_sources={str(Path(p).relative_to(ROOT)):audit.metrics.sha(Path(p)) for p in [__file__,SOURCE/'score_accounts.py',SOURCE/'reconstruct_account.py']})
        with target.open('x') as stream:json.dump(result,stream,indent=2,ensure_ascii=False);stream.write('\n')
        print('AUDITED',row['id'],'net',result['result']['net_model_return'],'NAV error',result['audit']['all_5m_equity_maximum_error_usdt'])

if __name__=='__main__':main()
