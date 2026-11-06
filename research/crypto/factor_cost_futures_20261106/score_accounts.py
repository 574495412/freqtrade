"""Audit all 5m account marks, costs and native-modeled funding from saved fills."""
import importlib.util
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd
from reconstruct_account import reconstruct

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('frozen_calendar_metrics',ROOT/'research/crypto/rd_program_20261105/p1/control_scorecard.py')
metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)

def score(row,plan):
    directory=ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'/row['id']
    for name,digest in json.loads((directory/'artifact_hashes.json').read_text()).items():
        if metrics.sha(directory/name)!=digest:raise ValueError('Artifact drift: '+name)
    registration=json.loads((directory/'registration.json').read_text())
    if registration['plan_sha256']!=metrics.sha(HERE/'plan.json'):raise ValueError('Registration mismatch')
    result=json.loads((directory/'result.json').read_text())
    cash=json.loads((directory/'cash_audit.json').read_text())
    if not cash['passed']:raise ValueError('Native cash audit failed')
    curve=pd.read_parquet(directory/'model_equity.parquet').set_index('date')
    expected=pd.date_range(row['start'],row['end'],freq='5min',inclusive='left')
    if not curve.index.equals(expected):raise ValueError('Incomplete account clock')
    archives=list((directory/'native_results').glob('*.zip'))
    if len(archives)!=1:raise ValueError('Ambiguous native export')
    with zipfile.ZipFile(archives[0]) as archive:
        names=[n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
        if len(names)!=1:raise ValueError('Ambiguous native JSON')
        trades=json.loads(archive.read(names[0]))['strategy'][row['strategy']]['trades']
    marks={};funding={}
    receipt=json.loads((ROOT/row['data_receipt']).read_text())
    for pair in {t['pair'] for t in trades}:
        mark_path=ROOT/row['mark_paths'][pair]
        if metrics.sha(mark_path)!=plan['input_hashes'][str(mark_path.relative_to(ROOT))]:raise ValueError('Mark drift')
        marks[pair]=pd.read_feather(mark_path).set_index('date').open
        stem=pair.replace('/','_').replace(':','_');base=ROOT/row['datadir']
        f=f'futures/{stem}-1h-funding_rate.feather';m=f'futures/{stem}-1h-mark.feather'
        if metrics.sha(base/f)!=receipt[f] or metrics.sha(base/m)!=receipt[m]:raise ValueError('Funding source drift')
        rates=pd.read_feather(base/f).set_index('date').funding_rate
        rates=rates.loc[(rates.index>=pd.Timestamp(row['start']))&(rates.index<pd.Timestamp(row['end']))]
        hourly=pd.read_feather(base/m).set_index('date').open.reindex(rates.index)
        if not np.isfinite(hourly).all() or not (hourly>0).all():raise ValueError('Unmatched funding mark')
        funding[pair]=rates*hourly
    rebuilt=reconstruct(trades,curve.index,marks,funding)
    equity_error=float(np.max(np.abs(rebuilt['equity']-curve.equity.to_numpy())))
    gross_error=float(np.max(np.abs(rebuilt['gross']-curve.gross.to_numpy())))
    if equity_error>1e-4 or gross_error>1e-4:raise ValueError(f'Account reconstruction mismatch: equity={equity_error}, gross={gross_error}')
    positive=sum(max(v,0.) for v in rebuilt['pairs'].values())
    audit=dict(all_5m_equity_maximum_error_usdt=equity_error,all_5m_gross_maximum_error_usdt=gross_error,
        passed=True,terminal_projected_funding=float(rebuilt['funding'][-1]),
        fees_before_endpoint=rebuilt['modeled_fees_before_endpoint'],
        one_sided_turnover_initial_capital=rebuilt['filled_notional_before_endpoint']/1000.,
        pairs=rebuilt['pairs'],campaigns=rebuilt['campaigns'],funding_scope=rebuilt['funding_scope'],
        maximum_positive_pair_share=max(max(v,0.)/positive for v in rebuilt['pairs'].values()) if positive else None,
        minimum_full_exit_campaigns=sum(t['exit_reason']=='RD1_minimum_full_exit' for t in trades),
        native_partial_reduction_fills=sum(o.get('ft_order_tag')=='RD1_candle_reduction' for t in trades for o in t['orders']),
        funding_event_order_certified=False,historical_capacity_verified=False)
    return dict(run_id=row['id'],strategy=row['strategy'],cost=row['cost'],window=row['window'],result=result,
        audit=audit,calendar=metrics.calendar_stats(curve.equity),drawdown=metrics.drawdown_stats(curve.equity),
        artifact_manifest_sha256=metrics.sha(directory/'artifact_hashes.json'))

if __name__=='__main__':
    plan=json.loads((HERE/'plan.json').read_text())
    for name,digest in plan['input_hashes'].items():
        if metrics.sha(ROOT/name)!=digest:raise ValueError('Source drift: '+name)
    rows=[score(row,plan) for row in plan['runs']]
    comparisons=[]
    for cost in ['base','stress']:
        f=next(r for r in rows if r['strategy']=='F2FuturesCandleCost' and r['cost']==cost)
        c=next(r for r in rows if r['strategy']=='AverageFuturesCandleControl' and r['cost']==cost)
        fr,cr=f['result']['net_model_return'],c['result']['net_model_return']
        comparisons.append(dict(cost=cost,net_return_difference=fr-cr,control_profitable=cr>0,
            drawdown_reduction=1-f['drawdown']['mdd']/c['drawdown']['mdd'] if c['drawdown']['mdd'] else None,
            positive_control_profit_retention=fr/cr if cr>0 else None,production_eligible=False))
    result=dict(rows=rows,F2_vs_control=comparisons,complete_model_accounts=len(rows),
        evidence='observed S candle-cost research with separate tradable-minimum routing profile',
        RD1_applied=False,independent_oos=False,production_eligible=False,orders_enabled=False)
    with (HERE/'results.json').open('x') as stream:json.dump(result,stream,indent=2,ensure_ascii=False);stream.write('\n')
    for row in rows:print(row['run_id'],row['result']['net_model_return'],row['drawdown']['mdd'],row['audit']['passed'])
