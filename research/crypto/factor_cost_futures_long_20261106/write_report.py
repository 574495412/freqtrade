"""Report saved, independently audited accounts. Never launches financial runs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def pct(value):
    return '—' if value is None else f'{value:.3%}'


def main():
    plan = read(HERE / 'plan.json')
    for name, digest in plan['input_hashes'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('Registered input changed: ' + name)
    long = []
    evidence = {}
    for registered in plan['runs']:
        path = HERE / 'audits' / (registered['id'] + '.json')
        row = read(path)
        directory = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106' / registered['id']
        assert row['run_id'] == registered['id'] and row['audit']['passed']
        assert row['result_sha256'] == sha(directory / 'result.json')
        assert row['artifact_manifest_sha256'] == sha(directory / 'artifact_hashes.json')
        for name, digest in read(directory / 'artifact_hashes.json').items():
            assert sha(directory / name) == digest, name
        for name, digest in row['audit_sources'].items():
            assert sha(ROOT / name) == digest, name
        evidence[str(path.relative_to(ROOT))] = sha(path)
        long.append(row)
    assert len(long) == 6
    short_path = HERE.parent / 'factor_cost_futures_20261106/results.json'
    short = read(short_path)['rows']
    evidence[str(short_path.relative_to(ROOT))] = sha(short_path)
    rows = long + short
    comparisons = []
    for window in ['L', 'S']:
        for cost in ['base', 'stress']:
            f, c = [next(r for r in rows if r['window'] == window and r['cost'] == cost
                         and r['strategy'] == strategy) for strategy in
                    ['F2FuturesCandleCost', 'AverageFuturesCandleControl']]
            fr, cr = f['result']['net_model_return'], c['result']['net_model_return']
            reduction = 1 - f['drawdown']['mdd'] / c['drawdown']['mdd'] if c['drawdown']['mdd'] else None
            retention = fr / cr if cr > 0 else None
            comparisons.append(dict(window=window, cost=cost, net_return_difference=fr-cr,
                control_profitable=cr > 0, drawdown_reduction=reduction,
                positive_control_profit_retention=retention,
                descriptive_matched_model_F2_rule_passed=bool(cr > 0 and reduction is not None
                    and reduction >= .2 and retention >= .8), full_RD1_qualified=False))
    btc_path = ROOT / 'research/crypto/rd_program_20261105/data_extension/mark_valuation_result.json'
    btc = []
    for r in read(btc_path)['rows']:
        if r['baseline'] != 'B1_BTC_HOLD':
            continue
        assert sha(ROOT / r['output']['path']) == r['output']['sha256']
        btc.append(dict(window=r['window'], cost=r['cost_id'],
            net_return=r['official_mark_net_return_conditional_cash'], mdd=r['mark_drawdown']['mdd'],
            output=r['output'], same_risk_control=False, cash_and_funding_conditional=True))
    evidence[str(btc_path.relative_to(ROOT))] = sha(btc_path)
    summary = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(), rows=long,
        F2_matched_comparisons=comparisons, btc_full_exposure_reference=btc,
        source_hashes=evidence, report_source_sha256=sha(Path(__file__)),
        complete_long_model_accounts=6, complete_futures_model_accounts=12,
        long_positive_accounts=sum(r['result']['net_model_return'] > 0 for r in long),
        evidence='observed retrospective candle-cost model; no independent holdout',
        RD1_applied=False, production_eligible=False, orders_enabled=False,
        reset_fold_qualification='not established; continuous inherited-state slices only',
        statistical_qualification='not established; no full-contract significance or promotion claim')
    lines = ['# 固定因子合约长短窗口对照', '',
        '40 币、1000 USDT、1x。L 为 2025-01-01 至 2026-09-01，S 为 2026-10-23 至 2026-11-05，均 UTC 右端不含。基础/压力单边成本 8/15bps，另计原生模型资金费。L/S 使用相同冻结因子、风险和最小金额退出路由。', '',
        '| 窗口 | 策略 | 成本 | 含持仓净收益 | 5m 最大回撤 | 自然结束 / 期末未平仓 |',
        '|---|---|---|---:|---:|---:|']
    for r in rows:
        v = r['result']
        lines.append(f"| {r['window']} | {r['strategy']} | {r['cost']} | {pct(v['net_model_return'])} | {pct(r['drawdown']['mdd'])} | {v['natural_campaigns']} / {v['terminal_open_campaigns']} |")
    lines += ['', f"长窗口正收益账户 {summary['long_positive_accounts']}/6；现金参考收益为 0%。完整费用、资金费、换手、贡献集中度、日/周收益和回撤持续时间见 results.json。", '',
        'F2 的冻结条件要求控制盈利、回撤降低至少 20%，并保留至少 80% 正利润。这里核对相同模型口径下的数值条件；完整 RD1 和上线资格仍未通过。', '',
        '| 窗口/成本 | F2 收益差 | 回撤降低 | 控制盈利 | 正利润保留 | 数值条件通过 |',
        '|---|---:|---:|---|---:|---|']
    for c in comparisons:
        lines.append(f"| {c['window']}/{c['cost']} | {pct(c['net_return_difference'])} | {pct(c['drawdown_reduction'])} | {c['control_profitable']} | {pct(c['positive_control_profit_retention'])} | {c['descriptive_matched_model_F2_rule_passed']} |")
    lines += ['', '连续账户开发区间切片，继承持仓和风险状态，不是重置账户验证折：', '',
        '| 策略/成本 | WF1 | WF2 | WF3 | WF4* | 首次风险警告 UTC |',
        '|---|---:|---:|---:|---:|---|']
    for r in long:
        values = ' | '.join(pct(s['net_return']) for s in r['continuous_development_slices'])
        lines.append(f"| {r['strategy']}/{r['cost']} | {values} | {r['warning_first_at']} |")
    lines += ['', '* WF4 截止 2026-08-31 23:55，缺下一午夜端点。风险限制后现金净值走平不是持续盈利。607 个完整 UTC 日用于日/年化统计；最后不足一天保留在总收益中。', '',
        '全部长窗口 5m 净值和敞口均由导出成交、原始资金费和 mark 独立重建，并与模拟曲线核对。资金费核对遵循原生区间端点模型，不能认证交易所真实结算与成交时序。', '',
        '| BTC 全敞口参考 | 净收益 | 5m 最大回撤 |', '|---|---:|---:|']
    for r in btc:
        lines.append(f"| {r['window']}/{r['cost']} | {pct(r['net_return'])} | {pct(r['mdd'])} |")
    lines += ['', 'BTC 为旧 P1 的 5m mark 重估参考，保留原现金/资金费假设；承担不同敞口，不能视为同风险控制，也没有新增账户。', '',
        '现货与合约最小金额路由不同，不混排为全局最优。历史盘口、首次接收时间、规则版本、全局因果和未观察留出仍未认证；`RD1_applied=false`。回测模型结果不构成实盘收益或容量证据。', '']
    if (HERE / 'results.json').exists() or (HERE / 'RESULTS.md').exists():
        raise FileExistsError('Preserve previous reports; use a new version')
    save(HERE / 'results.json', summary)
    with (HERE / 'RESULTS.md').open('x') as stream:
        stream.write('\n'.join(lines))
    print('Reported', len(long), 'long accounts; positive:', summary['long_positive_accounts'])


if __name__ == '__main__':
    main()
