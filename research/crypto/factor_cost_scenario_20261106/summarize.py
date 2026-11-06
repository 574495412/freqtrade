"""Read-only validation and compact evidence for the frozen six-account batch."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import zipfile
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')

plan=json.loads((HERE/'plan.json').read_text())
runtime=ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'
for name,digest in plan['input_hashes'].items():
    if sha(ROOT/name)!=digest:raise ValueError('Frozen source changed: '+name)
rows=[]
for row in plan['runs']:
    directory=runtime/row['id']
    hashes=json.loads((directory/'artifact_hashes.json').read_text())
    for name,digest in hashes.items():
        if sha(directory/name)!=digest:raise ValueError('Run artifact changed: '+name)
    result=json.loads((directory/'result.json').read_text())
    audit=json.loads((directory/'cash_audit.json').read_text())
    if not audit['passed']:raise ValueError('Failed cash audit')
    equity=pd.read_parquet(directory/'model_equity.parquet')
    if len(equity)!=3744 or equity.equity.isna().any():raise ValueError('Incomplete account NAV')
    with zipfile.ZipFile(next((directory/'native_results').glob('*.zip'))) as archive:
        name=next(n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json'))
        stats=json.loads(archive.read(name))['strategy'][row['strategy']]
    if any(t['open_timestamp']<pd.Timestamp(row['start']).timestamp()*1000 or t['close_timestamp']>=pd.Timestamp(row['end']).timestamp()*1000 for t in stats['trades']):
        raise ValueError('Native trading outside registered interval')
    contributions=defaultdict(float)
    for t in audit['trades']:contributions[t['pair']]+=t['reconstructed_pnl_usdt']
    record=dict(result, strategy=row['strategy'],cost=row['cost'],cash_audit_passed=True,
        cash_audit_aggregate_error_usdt=audit['aggregate_error_usdt'],
        native_terminal_contributions=dict(contributions),
        max_observed_gross_fraction=float((equity.gross/equity.equity).max()),
        correlation_complete_observations=int(equity.correlation_complete.sum()),
        artifact_manifest_sha256=sha(directory/'artifact_hashes.json'),result_sha256=sha(directory/'result.json'))
    rows.append(record)
btc=pd.read_feather(ROOT/plan['runs'][0]['datadir']/'BTC_USDT-5m.feather').set_index('date').open
start=pd.Timestamp(plan['runs'][0]['start']);end=pd.Timestamp(plan['runs'][0]['end'])-pd.Timedelta(minutes=5)
references={cost:float(btc.loc[end]/btc.loc[start]*(1-fee)/(1+fee)-1) for cost,fee in [('base',.0013),('stress',.002)]}
comparison=[]
for cost in ['base','stress']:
    f=next(r for r in rows if r['strategy']=='F2CandleCost' and r['cost']==cost)
    c=next(r for r in rows if r['strategy']=='AverageCandleControl' and r['cost']==cost)
    comparison.append(dict(cost=cost,net_return_difference=f['net_model_return']-c['net_model_return'],
        drawdown_reduction=1-f['max_5m_model_drawdown']/c['max_5m_model_drawdown'],
        profitable_control=c['net_model_return']>0,
        positive_profit_retention=f['net_model_return']/c['net_model_return'] if c['net_model_return']>0 else None,
        qualified=False))
failed=[dict(run_id=p.parent.name,**json.loads(p.read_text())) for p in runtime.glob('*/failure.json')]
summary=dict(scope='spot S observed 13-day candle cost scenario only',plan_sha256=sha(HERE/'plan.json'),
    completed_accounts=len(rows),prior_failed_attempts=len(failed),total_new_accounts_used=24+len(rows)+len(failed),
    cash_return=0,btc_buy_hold_reference_return=references,btc_reference_same_risk=False,rows=rows,
    F2_vs_control=comparison,failed_attempts=failed,
    conclusion='No candidate qualified; observed-window losses retained. No demonstrated profitable strategy or production promotion.',
    independent_oos=False,RD1_applied=False,production_eligible=False)
save(HERE/'results.json',summary)
lines=['# 固定因子首批经济结果','',
    '现货 36 币，1000 USDT，1x；2026-10-23 至 2026-11-05（UTC，右端不含）。全部使用同一模拟风险约束。以下收益含成本与未平仓估值；回撤来自 5m 事件前净值。','',
    '| 策略 | 单边成本 | 净收益 | 5m 最大回撤 | 自然结束 / 期末未平仓 |',
    '|---|---:|---:|---:|---:|']
for r in rows:
    lines.append(f"| {r['strategy']} | {'13' if r['cost']=='base' else '20'} bps | {r['net_model_return']:.3%} | {r['max_5m_model_drawdown']:.3%} | {r['natural_campaigns']} / {r['terminal_open_campaigns']} |")
lines+=['',f"现金收益为 0%；BTC 全仓买持参考：基础 {references['base']:.3%}、压力 {references['stress']:.3%}。BTC 参考承担不同敞口，不能作为同风险控制。",'',
    '结论：本批没有通过资格验收的候选。F2 即便亏损和回撤较小，也必须面对其控制账户是否盈利及完整跨窗门槛；不能将缩小亏损称为找到盈利策略。组合因子尚未产生已验证结果，不能把不同账户结果相加作为组合回测。','',
    '6 个账户的导出成交现金均独立核对通过；初次数据目录失败无成交，完整保留且保守占用 1 次预算。连同 24 个原官方账户共使用 31/80 次，余 49 次。','',
    '这只是已观察的短窗口成本研究，未验证独立样本外优势或完整 RD1 执行、盘口容量和真实成交。`RD1_applied=false`，不允许据此晋级上线。完整参数、数据与代码冻结见 plan.json；逐笔证据和净值文件的位置及 SHA-256 见 results.json 和各运行 artifact_hashes.json。','']
(HERE/'RESULTS.md').write_text('\n'.join(lines))
print('\n'.join(lines[:14]))
