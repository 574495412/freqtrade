"""Consolidate all 24 frozen cost accounts and reconcile the actual attempt ledger."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sources = {}
    rows = []
    paths = [(HERE.parent / 'factor_cost_long_20261106' / name, 'spot')
             for name in ['long_account_audit.json', 'short_account_audit.json']]
    paths += [(HERE.parent / name / 'results.json', 'futures')
              for name in ['factor_cost_futures_20261106', 'factor_cost_futures_long_20261106']]
    for path, market in paths:
        sources[str(path.relative_to(ROOT))] = sha(path)
        for row in read(path)['rows']:
            result = row['result']
            directory = RUNTIME / row['run_id']
            assert read(directory / 'result.json') == result
            for name, digest in read(directory / 'artifact_hashes.json').items():
                assert sha(directory / name) == digest, name
            assert abs(result['ending_model_equity'] / 1000 - 1 - result['net_model_return']) < 1e-10
            rows.append(dict(run_id=row['run_id'], market=market,
                factor=row['strategy'][:2] if row['strategy'].startswith(('F1', 'F2')) else 'Control',
                window=row['window'], cost=row['cost'], risk_profile=result['risk_profile'],
                net_return=result['net_model_return'], mdd=result['max_5m_model_drawdown'],
                natural_campaigns=result['natural_campaigns'], terminal_open_campaigns=result['terminal_open_campaigns'],
                result_sha256=sha(directory / 'result.json'),
                evidence_source=str(path.relative_to(ROOT))))
    assert len(rows) == 24 and len({r['run_id'] for r in rows}) == 24
    assert len({(r['market'], r['factor'], r['window'], r['cost']) for r in rows}) == 24
    ledger_path = RUNTIME / 'execution_ledger.jsonl'
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert len({r['run_id'] for r in ledger}) == len(ledger)
    completed = {r['run_id'] for r in ledger if r['state'] == 'COMPLETE'}
    assert completed == {r['run_id'] for r in rows}
    assert Counter(r['state'] for r in ledger) == {'COMPLETE': 24, 'FAILED': 1}
    for entry in ledger:
        filename, key = ('result.json', 'result_sha256') if entry['state'] == 'COMPLETE' else ('failure.json', 'failure_sha256')
        assert sha(RUNTIME / entry['run_id'] / filename) == entry[key]
    official_path = HERE.parent / 'official_factor_20261106/results.json'
    official = read(official_path)
    assert official['accounts'] == 24
    attempts = len(ledger) + official['accounts']
    assert attempts <= 80
    comparisons = []
    for market in ['spot', 'futures']:
        for window in ['L', 'S']:
            for cost in ['base', 'stress']:
                f, c = [next(r for r in rows if (r['market'], r['window'], r['cost'], r['factor'])
                             == (market, window, cost, factor)) for factor in ['F2', 'Control']]
                assert f['risk_profile'] == c['risk_profile']
                reduction = 1 - f['mdd'] / c['mdd'] if c['mdd'] else None
                retention = f['net_return'] / c['net_return'] if c['net_return'] > 0 else None
                comparisons.append(dict(market=market, window=window, cost=cost,
                    return_increment=f['net_return']-c['net_return'], drawdown_reduction=reduction,
                    control_profitable=c['net_return'] > 0, positive_control_profit_retention=retention,
                    numerical_F2_gate_passed=bool(c['net_return'] > 0 and reduction is not None
                        and reduction >= .2 and retention >= .8)))
    for path in [ledger_path, official_path, HERE / 'spot_curve_audit.json',
                 HERE / 'futures_short_account_details.json', HERE / 'engineering_preservation.json']:
        sources[str(path.relative_to(ROOT))] = sha(path)
    for r in rows:
        if r['market'] == 'futures' and r['window'] == 'L':
            path = HERE.parent / 'factor_cost_futures_long_20261106/details' / (r['run_id'] + '.json')
            detail = read(path)
            assert detail['result_sha256'] == r['result_sha256']
            sources[str(path.relative_to(ROOT))] = sha(path)
    summary = dict(observed_at_utc=datetime.now(timezone.utc).isoformat(), rows=rows,
        F2_matched_comparisons=comparisons, source_hashes=sources,
        report_source_sha256=sha(Path(__file__)), complete_model_accounts=24,
        positive_model_accounts=sum(r['net_return'] > 0 for r in rows),
        complete_factor_accounts=16, complete_control_accounts=8,
        new_account_attempts=attempts, new_account_limit=80, remaining_account_budget=80-attempts,
        failed_attempts_retained=1, old_P1_accounts_preserved_separately=32,
        old_determinism_checks_preserved_separately=2, model_comparison_complete=True,
        full_RD1_economic_comparison_complete=False, full_goal_complete=False,
        production_eligible=False, orders_enabled=False, independent_oos=False)
    lines = ['# 固定因子成本研究：24 个模型账户汇总', '',
        f"全部 24 个账户完成，正收益 {summary['positive_model_accounts']} 个：16 个 F1/F2 因子账户与 8 个同模拟风险控制账户。经济结果按固定模型解释，完整原目标仍未完成。", '',
        '每格为净收益 / 5m 最大回撤，已计未平仓盈亏及退出成本预留。', '',
        '| 市场 / 策略 | L 基础 | L 压力 | S 基础 | S 压力 |', '|---|---:|---:|---:|---:|']
    for market in ['spot', 'futures']:
        for factor in ['F1', 'F2', 'Control']:
            values = []
            for window, cost in [('L', 'base'), ('L', 'stress'), ('S', 'base'), ('S', 'stress')]:
                r = next(r for r in rows if (r['market'], r['factor'], r['window'], r['cost']) == (market, factor, window, cost))
                values.append(f"{r['net_return']:.3%} / {r['mdd']:.3%}")
            lines.append('| ' + market + ' / ' + factor + ' | ' + ' | '.join(values) + ' |')
    lines += ['', 'L 为 2025-01-01 至 2026-09-01，S 为 2026-10-23 至 2026-11-05，UTC 右端不含。每账户 1000 USDT、1x。现货 36 币、单边 13/20bps；合约 40 币、单边 8/15bps 加模型资金费。不同市场和最小金额路由不混排全局最优。', '',
        '| F2 配对条件 | 净收益差 | 回撤降低 | 控制盈利 | 保留正利润 | 数值门槛通过 |',
        '|---|---:|---:|---|---:|---|']
    for c in comparisons:
        retention = '—' if c['positive_control_profit_retention'] is None else f"{c['positive_control_profit_retention']:.3%}"
        reduction = '—' if c['drawdown_reduction'] is None else f"{c['drawdown_reduction']:.3%}"
        lines.append(f"| {c['market']}/{c['window']}/{c['cost']} | {c['return_increment']:.3%} | {reduction} | {c['control_profitable']} | {retention} | {c['numerical_F2_gate_passed']} |")
    lines += ['', '冻结的 F2 门槛要求控制盈利、回撤至少降低 20%、保留至少 80% 正利润；亏损对照的保留比例为未定义，不用负数相除制造通过。F1 对照现金零收益；BTC 全敞口参考承担不同风险，详见各市场报告，不能代替同风险控制。', '',
        'F1/F2 四个市场/因子组均因连续压力净收益不为正而淘汰。没有成本后盈利提升、合格新策略或盈利组合证据；保留失败，不调参救回候选。', '',
        '现货 12 个账户全部 5m 净值/敞口重建通过；合约 12 个账户另独立重建原生模型资金费。持仓年龄、DCA、费用、换手、贡献、完整日/周统计与回撤持续时间见引用的逐账户审计。合约长窗完整结果见 [长短窗口报告](../factor_cost_futures_long_20261106/RESULTS.md)，现货见 [现货报告](../factor_cost_long_20261106/RESULTS.md)。', '',
        '连续开发切片继承账户状态，不是重置验证折；WF4 缺下一午夜端点。已观察历史不是独立样本外。真实盘口、规则、接收时刻、资金费/成交顺序与完整 RD1 原生适配仍有缺项；没有统计资格或实盘认证。', '',
        f'实际台账为 {attempts}/80 次尝试：24 个官方账户、24 个模型账户和 1 次保留的技术失败；剩余 {80-attempts} 次不自动转为新策略搜索预算。旧 32 个 P1 账户和 2 次复核独立保留。paper 环境未操作，没有真实订单或外部通知。', '']
    if (HERE / 'complete_results.json').exists() or (HERE / 'RESULTS.md').exists():
        raise FileExistsError('Preserve previous final reports')
    with (HERE / 'complete_results.json').open('x') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    with (HERE / 'RESULTS.md').open('x') as stream:
        stream.write('\n'.join(lines))
    print('Complete model accounts:', len(rows), 'positive:', summary['positive_model_accounts'], 'attempts:', attempts)


if __name__ == '__main__':
    main()
