"""External read-only traversal of saved tree arrays and native model features."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save,sha

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root']);cache={};records=[]
    windows=0;cells=0;maximum=0.;unique_trees=0
    for candidate in plan['candidates']:
        if not candidate['strategy'].startswith('Rolling'):continue
        for cost in plan['total_cost_bps_per_side']:
            account=f'{candidate["name"]}_{cost}bps';directory=out/account/'freqai_audit'
            for meta in sorted(directory.glob('model_*.json')):
                record=json.loads(meta.read_text());key=meta.stem.split('_')[1]
                train_path=directory/f'train_{key}.parquet';predict_path=directory/f'predict_{key}.parquet';trees=directory/f'trees_{key}.npz'
                identity=(sha(train_path),sha(predict_path),sha(trees) if trees.exists() else 'PRIOR')
                if identity not in cache:
                    train=pd.read_parquet(train_path);predict=pd.read_parquet(predict_path)
                    columns=[c for c in predict if c not in ['date','down','up','do_predict']]
                    x=predict[columns].to_numpy(dtype=np.float32)
                    if record['prior']:
                        probabilities=np.array([train.label.eq('down').mean(),train.label.eq('up').mean()])
                        expected=np.tile(probabilities,(len(x),1))
                    else:
                        expected=np.zeros((len(x),2))
                        with np.load(trees,allow_pickle=False) as stored:
                            count=len([n for n in stored.files if n.startswith('left_')]);assert count==100
                            for i in range(count):
                                left,right=stored[f'left_{i}'],stored[f'right_{i}'];feature=stored[f'feature_{i}'];threshold=stored[f'threshold_{i}'];value=stored[f'value_{i}']
                                nodes=np.zeros(len(x),dtype=int);steps=0
                                while (left[nodes]>=0).any():
                                    active=np.flatnonzero(left[nodes]>=0);node=nodes[active]
                                    nodes[active]=np.where(x[active,feature[node]]<=threshold[node],left[node],right[node])
                                    steps+=1;assert steps<=5
                                leaf=value[nodes];expected+=leaf/leaf.sum(axis=1,keepdims=True)
                        expected/=count;unique_trees+=count
                    error=float(np.max(np.abs(expected-predict[['down','up']].to_numpy())))
                    assert error<1e-12
                    cache[identity]=dict(maximum_error=error,probability_cells=expected.size)
                proof=cache[identity];windows+=1;cells+=proof['probability_cells'];maximum=max(maximum,proof['maximum_error'])
                records.append(dict(account=account,train_stop=record['train_stop'],train_sha256=identity[0],prediction_sha256=identity[1],trees_sha256=identity[2],**proof))
    assert windows==696
    save(HERE/'external_model_recheck.json',dict(passed=True,model_windows=windows,unique_input_artifact_sets=len(cache),
         unique_saved_trees_traversed=unique_trees,total_account_probability_cells=cells,
         maximum_probability_error=maximum,no_estimators_refitted=True,no_signal_or_account_changes=True,records=records))
    print(json.dumps(dict(model_windows=windows,unique_sets=len(cache),unique_trees=unique_trees,maximum_probability_error=maximum)))


if __name__=='__main__':main()
