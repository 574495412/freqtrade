"""Read-only independent position/cash, full UTC-day and window attribution."""
import importlib.util
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
RUNTIME=ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

metrics=load('frozen_calendar_metrics',ROOT/'research/crypto/rd_program_20261105/p1/control_scorecard.py')
positions=load('frozen_position_audit',ROOT/'research/crypto/rd_program_20261105/p1/native/audit_positions.py')

def analyze(row,plan_path):
    plan=json.loads(plan_path.read_text())
    for name,digest in plan['input_hashes'].items():
        if metrics.sha(ROOT/name)!=digest:raise ValueError('Registered input drift: '+name)
    directory=RUNTIME/row['id']
    artifact_hashes=json.loads((directory/'artifact_hashes.json').read_text())
    for name,digest in artifact_hashes.items():
        if metrics.sha(directory/name)!=digest:raise ValueError('Run artifact drift: '+name)
    registration=json.loads((directory/'registration.json').read_text())
    if registration['plan_sha256']!=metrics.sha(plan_path):raise ValueError('Registration mismatch')
    result=json.loads((directory/'result.json').read_text())
    cash=json.loads((directory/'cash_audit.json').read_text())
    if not cash['passed']:raise ValueError('Cash audit failed')
    curve=pd.read_parquet(directory/'model_equity.parquet').set_index('date')
    expected=pd.date_range(row['start'],row['end'],freq='5min',inclusive='left')
    if not curve.index.equals(expected) or not np.isfinite(curve.equity).all():raise ValueError('Incomplete account calendar')
    if abs(curve.equity.iloc[0]-1000)>1e-8 or abs(curve.equity.iloc[-1]-result['ending_model_equity'])>1e-8:raise ValueError('NAV endpoint mismatch')
    archives=list((directory/'native_results').glob('*.zip'))
    if len(archives)!=1:raise ValueError('Ambiguous native export')
    with zipfile.ZipFile(archives[0]) as archive:
        names=[n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
        if len(names)!=1:raise ValueError('Ambiguous native JSON')
        trades=json.loads(archive.read(names[0]))['strategy'][row['strategy']]['trades']
    source=json.loads((ROOT/'research/crypto/rd_program_20261105/plan.json').read_text())
    cost=next(c for c in source['markets']['spot']['cost_scenarios'] if c['id']==row['cost'])
    prices={}
    receipt=json.loads((ROOT/row['data_receipt']).read_text())
    for pair in {t['pair'] for t in trades}:
        name=pair.replace('/','_')+'-5m.feather';path=ROOT/row['datadir']/name
        if metrics.sha(path)!=receipt[name]:raise ValueError('Price source drift')
        prices[pair]=pd.read_feather(path).set_index('date').open
    audit=positions.reconstruct(trades,curve.index,prices,curve.gross,result['ending_model_equity'],cost)
    if not audit['model_terminal_attribution_reconciled']:raise ValueError('Terminal marked account attribution unresolved')
    drawdown=metrics.drawdown_stats(curve.equity)
    if abs(drawdown['mdd']-result['max_5m_model_drawdown'])>1e-10:raise ValueError('Drawdown mismatch')
    folds=[]
    if row['window']=='L':
        for fold in source['walk_forward']['folds']:
            begin,requested_end=[pd.Timestamp(v,tz='UTC') for v in fold['validation']]
            end=min(requested_end,curve.index[-1]);part=curve.equity.loc[begin:end]
            if part.index[0]!=begin or part.index[-1]!=end:raise ValueError('Fold boundary missing')
            folds.append(dict(id=fold['id'],start=str(begin),requested_end=str(requested_end),observed_end=str(end),
                endpoint_complete=end==requested_end,net_return=float(part.iloc[-1]/part.iloc[0]-1),
                mdd=float((1-part/part.cummax()).max()),reset_account=False,
                inherited_positions_and_risk_state=True,independent_oos=False))
    return dict(run_id=row['id'],strategy=row['strategy'],cost=row['cost'],window=row['window'],
        result=result,drawdown=drawdown,calendar_metrics=metrics.calendar_stats(curve.equity),
        position_audit=audit,continuous_development_slices=folds,reset_fold_evidence=False,
        hard_halt_first_at=str(curve.index[curve.hard_halt][0]) if curve.hard_halt.any() else None,
        warning_first_at=str(curve.index[curve.warning][0]) if curve.warning.any() else None,
        day_halt_observations=int(curve.day_halt.sum()),
        gross_exposure_fraction_max=float((curve.gross/curve.equity).max()),
        provenance=dict(plan_sha256=metrics.sha(plan_path),artifact_manifest_sha256=metrics.sha(directory/'artifact_hashes.json'),
            equity_sha256=metrics.sha(directory/'model_equity.parquet'),result_sha256=metrics.sha(directory/'result.json')))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();p=json.loads(args.plan.read_text())
    rows=[analyze(row,args.plan) for row in p['runs']]
    with args.output.open('x') as f:json.dump(dict(rows=rows),f,indent=2,ensure_ascii=False);f.write('\n')
    for row in rows:print(row['run_id'],'position audit:',row['position_audit']['model_terminal_attribution_reconciled'],
        'complete UTC days:',row['calendar_metrics']['complete_day_count'])
