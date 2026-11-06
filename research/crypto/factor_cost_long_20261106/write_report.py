"""Render completed, independently audited L/S accounts without strategy search."""
import json
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
from analyze_account import ROOT,HERE,metrics

def read(p):return json.loads(p.read_text())
def save(p,v):
    with p.open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def pct(v):return '—' if v is None else f'{v:.3%}'

long=read(HERE/'long_account_audit.json')['rows']
short=read(HERE/'short_account_audit.json')['rows']
plan=read(HERE/'plan.json')
checks=read(HERE/'analysis_registration.json')
for name,digest in checks['sources'].items():
    if metrics.sha(ROOT/name)!=digest:raise ValueError('Analysis source drift')
rows=long+short
comparisons=[]
for window in ['L','S']:
    for cost in ['base','stress']:
        f=next(r for r in rows if r['window']==window and r['cost']==cost and r['strategy']=='F2CandleCost')
        c=next(r for r in rows if r['window']==window and r['cost']==cost and r['strategy']=='AverageCandleControl')
        fr,cr=f['result']['net_model_return'],c['result']['net_model_return']
        reduction=1-f['drawdown']['mdd']/c['drawdown']['mdd'] if c['drawdown']['mdd'] else None
        retain=fr/cr if cr>0 else None
        comparisons.append(dict(window=window,cost=cost,net_return_difference=fr-cr,drawdown_reduction=reduction,
            control_profitable=cr>0,positive_control_profit_retention=retain,
            descriptive_matched_model_F2_rule_passed=bool(cr>0 and reduction is not None and reduction>=.2 and retain>=.8),
            full_RD1_qualified=False,production_eligible=False))
btc={}
for window,source_plan in [('L',HERE/'plan.json'),('S',HERE.parent/'factor_cost_scenario_20261106/plan.json')]:
    row=read(source_plan)['runs'][0]
    prices=pd.read_feather(ROOT/row['datadir']/'BTC_USDT-5m.feather').set_index('date').open
    start=pd.Timestamp(row['start']);end=pd.Timestamp(row['end'])-pd.Timedelta(minutes=5)
    btc[window]={cost:float(prices.loc[end]/prices.loc[start]*(1-fee)/(1+fee)-1) for cost,fee in [('base',.0013),('stress',.002)]}
summary=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),model='unchanged V1 candle cost model',
    new_complete_accounts=6,total_complete_model_accounts=12,prior_failed_model_attempts=1,
    total_new_account_attempts=37,remaining_new_account_budget=43,
    F2_matched_comparisons=comparisons,btc_full_exposure_reference=btc,btc_same_risk=False,
    long_positive_account_count=sum(r['result']['net_model_return']>0 for r in long),
    short_positive_account_count=sum(r['result']['net_model_return']>0 for r in short),
    long_audit_sha256=metrics.sha(HERE/'long_account_audit.json'),short_audit_sha256=metrics.sha(HERE/'short_account_audit.json'),
    full_RD1_qualified=False,independent_oos=False,production_eligible=False,orders_enabled=False,
    statistical_qualification='not established; missing full execution/causality and reset-fold qualification is not a pass')
save(HERE/'results.json',summary)
lines=['# 固定因子现货长短窗口对照','',
    '相同 1000 USDT、1x、36 币和模拟风险约束。L 为 2025-01-01 至 2026-09-01；S 为 2026-10-23 至 2026-11-05，均 UTC 右端不含。基础/压力单边成本分别 13/20bps。信号、风险和执行源码与首批完全一致。','',
    '| 窗口 | 策略 | 成本 | 含持仓净收益 | 5m 最大回撤 | 自然结束 / 期末未平仓 |',
    '|---|---|---|---:|---:|---:|']
for r in rows:
    v=r['result'];lines.append(f"| {r['window']} | {r['strategy']} | {r['cost']} | {pct(v['net_model_return'])} | {pct(r['drawdown']['mdd'])} | {v['natural_campaigns']} / {v['terminal_open_campaigns']} |")
lines+=['',f"长窗口盈利账户 {summary['long_positive_account_count']}/6；短窗口盈利账户 {summary['short_positive_account_count']}/6。以上为扣费并计入未实现盈亏的账户结果，不能从盈利数量推导上线资格。",'',
    'F2 必须在控制账户盈利的前提下，回撤降低至少 20%、保留至少 80% 正利润。以下只核对同模拟口径的必要数值条件，完整 RD1 与独立验证仍未通过：','',
    '| 窗口/成本 | F2 相对控制净收益差 | 回撤降低 | 控制盈利 | 正利润保留 | 数值条件 |',
    '|---|---:|---:|---|---:|---|']
for c in comparisons:lines.append(f"| {c['window']}/{c['cost']} | {pct(c['net_return_difference'])} | {pct(c['drawdown_reduction'])} | {c['control_profitable']} | {pct(c['positive_control_profit_retention'])} | {c['descriptive_matched_model_F2_rule_passed']} |")
lines+=['','连续账户开发区间切片（不是重置账户验证折，持仓及风险状态承接）：','',
    '| 策略/成本 | WF1 | WF2 | WF3 | WF4* |', '|---|---:|---:|---:|---:|']
for r in long:lines.append('| '+r['strategy']+'/'+r['cost']+' | '+' | '.join(pct(x['net_return']) for x in r['continuous_development_slices'])+' |')
lines+=['','* WF4 观察截止 2026-08-31 23:55 UTC，缺下一个 00:00 端点，不能冒充完整折。若风险停机后净值保持不变，该切片仍如实保留为零，不重新启动账户来美化结果。','',
    '独立导出成交重建核对了完整敞口和期末持仓净值；费用、资金费、换手、集中度、持仓年龄、完整 UTC 日/周、ES95、Sharpe、Sortino、Calmar 与回撤持续时间见两个 account_audit.json。日统计只用准确的午夜边界，末尾不足一天部分不参与日度或年化计算，但仍计入总收益。', '',
    f"现金参考收益为 0%；BTC 全敞口买持参考 L 基础/压力为 {pct(btc['L']['base'])}/{pct(btc['L']['stress'])}，S 为 {pct(btc['S']['base'])}/{pct(btc['S']['stress'])}。BTC 承担不同敞口，不是同风险控制。",'',
    '没有新的上线资格或组合因子结论。历史首次接收、盘口容量、规则、全局因果与最终未观察留出仍缺证据。`RD1_applied=false`；5m OPEN 模拟不能认证实盘执行或盘中最大回撤。冻结旧基线、失败、参数与 8 次诊断预算均保持不变。','',
    '截至本批 37/80 次账户尝试、剩余 43 次。无 paper 或真实交易环境修改。', '']
(HERE/'RESULTS.md').write_text('\n'.join(lines))
print('\n'.join(lines[:15]))
