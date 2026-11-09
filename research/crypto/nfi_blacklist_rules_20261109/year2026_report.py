# ruff: noqa: E501
"""User-prioritized 2026 comparison; retain deferred sensitivity as unknown."""

import json

import pandas as pd

from research.crypto.nfi_blacklist_rules_20261109.analyze import (
    account,
    auxiliary_reference,
    patterns,
)
from research.crypto.nfi_blacklist_rules_20261109.inputs import HERE, read, ref, save


def main():
    scope = read(HERE / "user-scope-change.json")
    if not scope["both_cost_promotion_gate_not_evaluated"]:
        raise ValueError("Unexpected reporting scope")
    rows = [account(name, 15) for name in ("structural", "native_risk")]
    a, b = rows
    reference = auxiliary_reference()
    (old_author,) = [r for r in reference["accounts"] if r["cost_bps_per_side"] == 15]
    if any(r["valuation_points"] != 89917 for r in rows):
        raise ValueError("Incomplete 2026 native observation calendar")
    changes = {
        "net_return_delta_pp_risk_minus_structural": b["net_return_pct"] - a["net_return_pct"],
        "maximum_drawdown_delta_pp_risk_minus_structural": b["maximum_5m_mark_drawdown_pct"]
        - a["maximum_5m_mark_drawdown_pct"],
        "addition_fill_delta_risk_minus_structural": b["actual_addition_fills"]
        - a["actual_addition_fills"],
    }
    ranked = sorted(
        [r for r in rows if r["feasibility_passed"]],
        key=lambda r: r["net_return_pct"],
        reverse=True,
    )
    verdict = {
        "primary_requested_2026_15bps_comparison_complete": True,
        "highest_net_return_with_30pct_drawdown_at_15bps": ranked[0]["policy"] if ranked else None,
        "risk_arm_improves_return_without_worse_drawdown_at_15bps": b["feasibility_passed"]
        and changes["net_return_delta_pp_risk_minus_structural"] > 0
        and changes["maximum_drawdown_delta_pp_risk_minus_structural"] <= 0,
        "frozen_both_cost_preference_verdict": "NOT_EVALUATED_8BPS_DEFERRED_BY_USER",
        "independent_out_of_sample_validation": False,
        "production_eligible": False,
        "paper_service_changed": False,
        "orders_enabled": False,
    }
    result = {
        "window": {k: read(HERE / "structural-plan.json")[k] for k in ("start", "comparison_end")},
        "initial_equity_usdt": 1000,
        "leverage": 3,
        "max_positions": 12,
        "daily_selected_coins": 80,
        "cost_bps_per_side": 15,
        "returns_cumulative_not_annualized": True,
        "accounts": rows,
        "risk_minus_structural": changes,
        "old_author_blacklist_15bps_auxiliary_only": {
            "status": reference["status"],
            "source": reference["source"],
            "not_an_unbiased_control": True,
            "account": old_author,
        },
        "verdict": verdict,
        "patterns": patterns(),
        "user_scope_change": ref(HERE / "user-scope-change.json"),
        "original_protocol": ref(HERE / "PROTOCOL.md"),
        "exact_known_account_parity": ref(HERE / "parity-result.json"),
        "contract_identity_audit": ref(HERE / "identity-calendar-audit.json"),
        "causal_clock_audit": ref(HERE / "causal-clock-audit.json"),
        "source_audit": ref(HERE / "source-audit.json"),
        "preserved_disk_guard_attempt": ref(HERE / "disk-recovery.json"),
        "full_point_in_time_universe": False,
    }
    save(HERE / "comparison-2026-15bps.json", result)
    fields = (
        "policy",
        "cost_bps_per_side",
        "net_return_pct",
        "maximum_5m_mark_drawdown_pct",
        "natural_completed_trades",
        "actual_addition_fills",
        "held_addition_fills_outside_current_pool",
        "native_modeled_liquidations",
        "feasibility_passed",
    )
    pd.DataFrame([{k: r[k] for k in fields} for r in rows]).to_csv(
        HERE / "comparison-2026-15bps.csv", index=False
    )
    lines = [
        "# 2026年黑名单规则比较（15bps成本）",
        "",
        "按用户要求先交付今年主比较，暂缓尚未启动的两组8bps敏感性测试。原四账户协议、阈值和失败记录均保留；本报告不宣称完成双成本晋级门槛。更早行情只用于指标预热，不计入收益区间。",
        "",
        "2026-01-01 00:00至2026-11-09 05:00 UTC；初始1,000虚拟USDT、3倍逐仓、最多12仓、每日80币；每边15bps成本假设及原生资金费。收益为累计净收益，回撤来自含未平仓浮亏和剩余平仓费用的89,917个5分钟净值点，不是盘中最坏回撤或实盘亏损保证。",
        "",
        "| 规则 | 扣费后收益 | 最大5分钟净值回撤 | 自然平仓 | 加仓成交 | 移出币池后加仓 | 模型强平 | 30%门槛 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    names = {"structural": "仅产品类型排除＋动态成交额", "native_risk": "再叠加原生区间/波动过滤"}
    for r in rows:
        lines.append(
            f"| {names[r['policy']]} | {r['net_return_pct']:.2f}% | {r['maximum_5m_mark_drawdown_pct']:.2f}% | {r['natural_completed_trades']} | {r['actual_addition_fills']} | {r['held_addition_fills_outside_current_pool']} | {r['native_modeled_liquidations']} | {'PASS' if r['feasibility_passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        f"本次15bps场景中，满足冻结研究准入的收益最高方案为：{names[verdict['highest_net_return_with_30pct_drawdown_at_15bps']] if ranked else '无'}。额外风险过滤相对结构规则的收益差为{changes['net_return_delta_pp_risk_minus_structural']:.2f}个百分点、回撤差为{changes['maximum_drawdown_delta_pp_risk_minus_structural']:.2f}个百分点、加仓成交差为{changes['addition_fill_delta_risk_minus_structural']}次。这个结论只适用于本次成本和历史范围，不能推广为所有价格过滤无效。",
        "",
        f"旧作者币名/事件黑名单的同窗口15bps回顾性参考为：收益{old_author['net_return_pct']:.2f}%、回撤{old_author['maximum_5m_mark_drawdown_pct']:.2f}%、加仓{old_author['actual_addition_fills']}次。新结构规则收益更低、回撤更高，因此本轮没有证明比旧名单更赚钱。旧参考使用后出的名单回填历史，存在后见偏差，不能作为无偏控制，也不能为了追回该收益重新加入事后亏损币惩罚。",
    ]
    lines += [
        "",
        "```json",
        json.dumps({"changes": changes, "verdict": verdict}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 同一持续账户的半年表现",
        "",
        "| 规则 | 上半年收益 | 7月至截止日收益 |",
        "|---|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {names[r['policy']]} | {r['periods'][1]['net_return'] * 100:.2f}% | {r['periods'][2]['net_return'] * 100:.2f}% |"
        )
    lines += [
        "",
        "## 修改后的研究规则与规律",
        "",
        "1. 保留7条继承的产品类型规则，取消永久币名/板块/历史亏损惩罚；至少60根已完成日线，前一完整UTC日成交额为正，按成交额选前80。原名单当前仍有效候选45个，其中USTC保留类型排除，恢复44个名字进入筛选。",
        "",
        "2. 第二组使用未改写的原生RangeStabilityFilter(10,0.01,0.99)与VolatilityFilter(10,0.05,0.50)，实际读取11根已完成日线，每日重新判断并允许重新入选。它明显排除低波动币：全样本波动下限排除3,724个币日、上限93个币日；BTC被排除128天。换入次数由2,768增至3,270，不能把它笼统解释为降低风险或等同于成交换手。",
        "",
        "3. 当前名单回填年初会引入后见之明。同一343候选集合，2025年末快照命中66币、原冻结版本105币、最新版本107币。旧高收益名单结果仅为回顾性辅助参考。",
        "",
        "4. 币名不能代替合约身份：旧Litentry与新Lighter复用了LIT。已排除新合约上市前的归档静止价格，并核对267合约日线/5分钟输入的身份时钟。",
        "",
        "5. 新开仓准入与已有仓位风控是两个环节。币种移出币池后，原NFI仍可加仓及退出；不能把名单过滤描述成账户止损。事件禁入还应记录合约、事件、生效/可知时间、证据及解除条件。价格窗口恢复不能证明黑客攻击或供应完整性风险已经解除。本轮未重建历史事件公告流。",
        "",
        "## 审计与边界",
        "",
        "实际原生Freqtrade Backtesting负责成交、钱包、资金费和加减仓。本地历史时钟/币池桥接调用未修改的原生风险计算；不能将其标作上游原生历史过滤支持。无损存储的控制复现逐点一致：89,917净值点及244笔原生交易记录。两账户均验证实际退出码、完整日历、Decimal逐单现金、实际每日币池和初始开仓轨迹。",
        "",
        "风险组首次触发磁盘保护（退出码73），记录保留且未计作策略结果；清理带哈希清单的生成缓存后，以同一计划补跑，经济参数未变。8bps目录中的DEFERRED_BY_USER仅为调度停止标记，没有启动该成本账户。",
        "",
        "仍使用当前作者候选、当前市场/杠杆档位和后出的策略版本，存在幸存者与来源选择偏差；不是完整历史时点币池或独立样本外验证。双成本结论待补；研究结果不自动晋级，现有模拟盘未切换，没有实盘下单。",
        "",
    ]
    (HERE / "REPORT-2026-15bps.md").write_text("\n".join(lines))
    save(
        HERE / "verification-2026-15bps.json",
        {
            "passed": True,
            "scope": "User-prioritized 2026 two-policy15bps comparison",
            "completed_policy_accounts": 2,
            "deferred_8bps_accounts": 2,
            "comparison": ref(HERE / "comparison-2026-15bps.json"),
            "report": ref(HERE / "REPORT-2026-15bps.md"),
            "analyzer": ref(HERE / "year2026_report.py"),
            "both_cost_promotion_gate_evaluated": False,
            "orders_enabled": False,
        },
    )
    print(json.dumps({"changes": changes, "verdict": verdict}, ensure_ascii=False))


if __name__ == "__main__":
    main()
