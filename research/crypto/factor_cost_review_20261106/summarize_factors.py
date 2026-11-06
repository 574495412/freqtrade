"""Summarize the frozen 16 factor accounts, without waiting for control ranking."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sources = {}
    rows = []
    paths = [(HERE.parent / 'factor_cost_long_20261106' / name, 'spot')
             for name in ['long_account_audit.json', 'short_account_audit.json']]
    paths += [(HERE.parent / 'factor_cost_futures_20261106/results.json', 'futures')]
    paths += [(path, 'futures') for path in sorted(
        (HERE.parent / 'factor_cost_futures_long_20261106/audits').glob('*_F[12]FuturesCandleCost_*.json'))]
    for path, market in paths:
        sources[str(path.relative_to(ROOT))] = sha(path)
        content = read(path)
        for row in content.get('rows', [content]):
            if not row['strategy'].startswith(('F1', 'F2')):
                continue
            output = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106' / row['run_id']
            expected = row.get('result_sha256', row.get('provenance', {}).get('result_sha256'))
            digest = sha(output / 'result.json')
            if expected is not None:
                assert digest == expected
            assert read(output / 'result.json') == row['result']
            for name, value in read(output / 'artifact_hashes.json').items():
                assert sha(output / name) == value, name
            result = row['result']
            rows.append(dict(run_id=row['run_id'], market=market, factor=row['strategy'][:2],
                window=row['window'], cost=row['cost'], risk_profile=result['risk_profile'],
                net_return=result['net_model_return'], mdd=result['max_5m_model_drawdown'],
                result_sha256=digest, terminal_open_campaigns=result['terminal_open_campaigns'],
                source=str(path.relative_to(ROOT))))
    assert len(rows) == 16 and len({r['run_id'] for r in rows}) == 16
    decisions = []
    for market in ['spot', 'futures']:
        for factor in ['F1', 'F2']:
            group = [r for r in rows if r['market'] == market and r['factor'] == factor]
            assert {(r['window'], r['cost']) for r in group} == {(w, c) for w in ['L', 'S'] for c in ['base', 'stress']}
            pressure_positive = all(r['net_return'] > 0 for r in group if r['cost'] == 'stress')
            decisions.append(dict(market=market, factor=factor,
                continuous_stress_net_positive=pressure_positive,
                decision='REJECTED_COST_MODEL' if not pressure_positive else 'OTHER_GATES_UNRESOLVED',
                production_eligible=False))
    output = dict(rows=rows, decisions=decisions, source_hashes=sources,
        summary_source_sha256=sha(Path(__file__)), complete_factor_accounts=16,
        positive_factor_accounts=sum(r['net_return'] > 0 for r in rows),
        excluded_pending_control_accounts=2, complete_control_comparison=False,
        evidence='Observed retrospective candle-cost assumptions; no independent OOS or full RD1 acceptance',
        new_financial_runs=0, RD1_applied=False, production_eligible=False)
    lines = ['# 固定 F1/F2 因子：16 个成本模型账户结果', '',
        f"16 个因子账户已全部完成，其中正收益 {output['positive_factor_accounts']} 个。合约长窗口的两笔控制账户仍在运行或排队，尚未完成最终 F2 配对比较。", '',
        '表格每格为净收益 / 5m 最大回撤，均包含未平仓估值和退出成本预留；合约另计原生模型资金费。', '',
        '| 市场 / 因子 | L 基础 | L 压力 | S 基础 | S 压力 |', '|---|---:|---:|---:|---:|']
    for market in ['spot', 'futures']:
        for factor in ['F1', 'F2']:
            values = []
            for window, cost in [('L', 'base'), ('L', 'stress'), ('S', 'base'), ('S', 'stress')]:
                r = next(r for r in rows if (r['market'], r['factor'], r['window'], r['cost']) == (market, factor, window, cost))
                values.append(f"{r['net_return']:.3%} / {r['mdd']:.3%}")
            lines.append('| ' + market + ' / ' + factor + ' | ' + ' | '.join(values) + ' |')
    lines += ['', 'L：2025-01-01 至 2026-09-01；S：2026-10-23 至 2026-11-05。均 UTC 右端不含，初始 1000 USDT、1x。现货 36 币、单边 13/20bps；合约 40 币、单边 8/15bps 加资金费。不同市场与最小金额处理方式不混排全局最优。', '',
        '本轮四个市场/因子候选都未满足冻结的“连续压力成本净收益为正”必要条件，因此按该成本模型淘汰。F1 均未超过零收益现金；F2 的控制比较仍须保留完整结果，不能把少亏或降低风险等同于盈利因子。没有登记或验证盈利组合。', '',
        '这是固定候选在已观察历史和明确模拟假设下的淘汰结论。完整 RD1、全局因果、盘口容量、历史规则、真实结算时序和最终独立留出仍未通过；不能把负结果总结写成整个目标完成，也不据此调参救回候选。', '',
        '来源及结果哈希见 factor_results.json。旧 P1、官方样例和失败记录保留；本汇总没有新增金融账户。', '']
    if (HERE / 'factor_results.json').exists() or (HERE / 'FACTOR_RESULTS.md').exists():
        raise FileExistsError('Preserve prior outputs')
    with (HERE / 'factor_results.json').open('x') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    with (HERE / 'FACTOR_RESULTS.md').open('x') as stream:
        stream.write('\n'.join(lines))
    print('Summarized 16 completed factor accounts;', output['positive_factor_accounts'], 'positive')


if __name__ == '__main__':
    main()
