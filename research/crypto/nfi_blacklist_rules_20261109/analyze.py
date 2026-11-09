# ruff: noqa: E501, C901
"""Read and reconcile completed native accounts; never generate alternative fills."""

import json
from collections import Counter

import pandas as pd

from research.crypto.nfi80_capacity_20261109 import analyze as metrics
from research.crypto.nfi_blacklist_rules_20261109.inputs import (
    HERE,
    OUT,
    ROOT,
    read,
    ref,
    save,
    sha,
)
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, size_audit


def account(name, cost):
    plan = read(HERE / f"{name}-plan.json")
    folder = OUT / "accounts" / f"{name}_{cost}bps"
    if read(folder / "registered_plan.json") != plan:
        raise ValueError("Actual registered native plan differs")
    candidate = plan["candidates"][0]
    config = read(folder / "effective_config.json")
    if config["strategy"] != "FlatTopBoost":
        raise ValueError("Actual native strategy class differs")
    for filename, record in candidate["source_files"].items():
        if sha(folder / "strategies" / filename) != record["sha256"]:
            raise ValueError("Actual copied strategy source differs")
    if (
        config["exchange"]["pair_blacklist"]
        != read(candidate["config"]["path"])["exchange"]["pair_blacklist"]
    ):
        raise ValueError("Actual structural blacklist differs")
    contract = {
        k: plan[k] for k in ("start", "end", "comparison_end", "pairs", "research_drawdown_limit")
    }
    contract_path = HERE / f"{name}-account-contract.json"
    save(contract_path, contract)
    metrics.OUT, metrics.PROTOCOL = OUT / "accounts", contract_path
    metrics.CASES = {"structural": (3, 12), "native_risk": (3, 12)}
    row = metrics.account(name, cost)
    trades, cash = native_trades(plan, folder, "FlatTopBoost", cost)
    storage = read(folder / "native_row_storage_audit.json")
    if not storage["exact_serialized_roundtrip"] or storage["pair_count"] != len(plan["pairs"]):
        raise ValueError("Native row storage audit failed")
    schedule = read(plan["historical_schedule"]["path"])["days"]
    observed = read(folder / "actual_historical_pairlists.json")
    if set(observed) != set(schedule) or any(
        observed[d]["pairs"] != schedule[d]["pairs"] for d in schedule
    ):
        raise ValueError("Actual native daily pool differs from frozen causal schedule")
    checks = read(folder / "initial_entry_checks.json")
    accepted = [r for r in checks if r["accepted"]]
    if any(not r["inside_current_pool"] for r in accepted):
        raise ValueError("Initial order accepted outside current pool")
    accepted_keys = {(r["pair"], int(pd.Timestamp(r["time"]).timestamp() * 1000)) for r in accepted}
    held_adds = held_exits = prior_accepted = 0
    pair_rows = {}
    restored = set(read(HERE / "source-audit.json")["restored_named_candidates"])
    risk_days = read(OUT / "native_risk-schedule.json")["days"]
    entry_diagnostics = []
    cutoff = int(pd.Timestamp(plan["comparison_end"]).timestamp() * 1000)
    for trade in trades:
        pair = trade["pair"]
        if (pair, int(trade["open_timestamp"])) not in accepted_keys:
            raise ValueError("Native campaign has no accepted initial-order trace")
        opened_day = (
            pd.to_datetime(int(trade["open_timestamp"]), unit="ms", utc=True).date().isoformat()
        )
        risk_day = risk_days[opened_day]
        entry_diagnostics.append(
            {
                "pair": pair,
                "initial_day": opened_day,
                "native_terminal_pnl_usdt": float(trade["profit_abs"]),
                "natural_close": trade["exit_reason"] != "force_exit"
                and int(trade["close_timestamp"]) < cutoff,
                "risk_pool_selected_at_initial": pair in risk_day["pairs"],
                "risk_first_failing_filter_at_initial": risk_day["risk_exclusions"].get(pair),
            }
        )
        entries = 0
        for order in trade["orders"]:
            day = (
                pd.to_datetime(int(order["order_filled_timestamp"]), unit="ms", utc=True)
                .date()
                .isoformat()
            )
            inside = pair in schedule[day]["pairs"]
            if order["ft_is_entry"]:
                if entries == 0 and not inside:
                    prior_accepted += 1
                elif entries > 0 and not inside:
                    held_adds += 1
                entries += 1
            elif not inside:
                held_exits += 1
        stats = pair_rows.setdefault(
            pair,
            {
                "pair": pair,
                "restored_named_candidate": pair in restored,
                "campaigns": 0,
                "natural_closes": 0,
                "natural_closed_pnl_usdt": 0.0,
                "native_terminal_pnl_usdt": 0.0,
                "addition_fills": 0,
            },
        )
        stats["campaigns"] += 1
        stats["addition_fills"] += max(0, entries - 1)
        stats["native_terminal_pnl_usdt"] += float(trade["profit_abs"])
        if trade["exit_reason"] != "force_exit" and int(trade["close_timestamp"]) < cutoff:
            stats["natural_closes"] += 1
            stats["natural_closed_pnl_usdt"] += float(trade["profit_abs"])
    save(
        folder / "report-pair-contributions.json",
        {
            "basis": "Descriptive native campaigns; terminal PnL includes forced terminal settlement and differs from common marked NAV. Not a counterfactual causal attribution.",
            "pairs": sorted(pair_rows.values(), key=lambda r: r["native_terminal_pnl_usdt"]),
        },
    )
    save(
        folder / "report-entry-eligibility-diagnostics.json",
        {
            "basis": "Descriptive actual native campaigns against the frozen risk pool at their initial clock. These are not alternative account fills, returns or causal PnL attribution.",
            "worst_eight_native_terminal_campaigns": sorted(
                entry_diagnostics, key=lambda r: r["native_terminal_pnl_usdt"]
            )[:8],
            "restored_names_natural_closed_pnl_usdt": sum(
                r["natural_closed_pnl_usdt"]
                for r in pair_rows.values()
                if r["restored_named_candidate"]
            ),
            "restored_names_campaigns": sum(
                r["campaigns"] for r in pair_rows.values() if r["restored_named_candidate"]
            ),
            "restored_names_addition_fills": sum(
                r["addition_fills"] for r in pair_rows.values() if r["restored_named_candidate"]
            ),
        },
    )
    row.update(
        policy=name,
        actual_native_strategy="FlatTopBoost",
        independent_fill_cash=cash,
        initial_stake_audit=size_audit(plan, folder, "FlatTopBoost", trades),
        selection_trace=ref(folder / "actual_historical_pairlists.json"),
        initial_order_trace=ref(folder / "initial_entry_checks.json"),
        new_initial_orders_outside_current_pool=0,
        prior_accepted_initial_fills_outside_pool=prior_accepted,
        held_addition_fills_outside_current_pool=held_adds,
        exit_fills_outside_current_pool=held_exits,
        native_row_storage_audit=ref(folder / "native_row_storage_audit.json"),
        pair_contributions=ref(folder / "report-pair-contributions.json"),
        entry_eligibility_diagnostics=ref(folder / "report-entry-eligibility-diagnostics.json"),
        feasibility_passed=row["net_return_pct"] > 0 and row["research_risk_gate_passed"],
    )
    return row


def patterns():
    summary = {}
    for name in ("structural", "native_risk"):
        plan = read(HERE / f"{name}-plan.json")
        days = read(plan["historical_schedule"]["path"])["days"]
        reasons = Counter()
        threshold_sides = Counter()
        entries = exits = reentries = 0
        seen, previous = set(), set()
        for day in sorted(days):
            row = days[day]
            current = set(row["pairs"])
            reasons.update(row["risk_exclusions"].values())
            for pair, method in row["risk_exclusions"].items():
                value = row["risk_values"][pair][method]
                low, high = (0.01, 0.99) if method == "RangeStabilityFilter" else (0.05, 0.50)
                side = (
                    "below_minimum"
                    if value < low
                    else "above_maximum"
                    if value > high
                    else "unknown"
                )
                threshold_sides[f"{method}.{side}"] += 1
            entries += len(current - previous)
            exits += len(previous - current)
            reentries += len((current - previous) & seen)
            seen |= current
            previous = current
        summary[name] = {
            "days": len(days),
            "selected_union": len(seen),
            "membership_entries_including_initial": entries,
            "membership_exits": exits,
            "membership_reentries": reentries,
            "risk_excluded_pair_days_by_first_failing_filter": dict(reasons),
            "risk_excluded_pair_days_by_threshold": dict(threshold_sides),
            "major_coin_diagnostics": {
                pair: {
                    "selected_days": sum(pair in r["pairs"] for r in days.values()),
                    "risk_excluded_days": sum(pair in r["risk_exclusions"] for r in days.values()),
                }
                for pair in (
                    "BTC/USDT:USDT",
                    "ETH/USDT:USDT",
                    "BNB/USDT:USDT",
                    "SOL/USDT:USDT",
                    "BTCDOM/USDT:USDT",
                )
            },
            "minimum_eligible": min(r["eligible_after_risk"] for r in days.values()),
            "maximum_eligible": max(r["eligible_after_risk"] for r in days.values()),
            "membership_change_is_not_actual_trade_turnover": True,
        }
    return summary


def auxiliary_reference():
    source = ROOT / "research/crypto/nfi80_historical_universe_20261109/v003/comparison.json"
    rows = [
        r
        for r in read(source)["accounts"]
        if r["variant"] == "flatboost" and r["universe"] == "daily"
    ]
    if len(rows) != 2:
        raise ValueError("Ambiguous retrospective author reference")
    for row in rows:
        for key in (
            "native_archive",
            "native_result",
            "observed_equity",
            "cash_audit",
            "completion",
        ):
            if sha(row[key]["path"]) != row[key]["sha256"]:
                raise ValueError("Auxiliary reference evidence hash differs")
    return {
        "status": "AUXILIARY_RETROSPECTIVE_ONLY",
        "not_an_unbiased_control": True,
        "source": ref(source),
        "accounts": rows,
    }


def main():
    if not read(HERE / "parity-result.json")["passed"]:
        raise ValueError("Exact known-account parity missing")
    rows = [account(name, cost) for cost in (15, 8) for name in ("structural", "native_risk")]
    feasible = {
        name: all(r["feasibility_passed"] for r in rows if r["policy"] == name)
        for name in ("structural", "native_risk")
    }
    changes = []
    rankings = {}
    for cost in (15, 8):
        a, b = [
            next(r for r in rows if r["policy"] == name and r["cost_bps_per_side"] == cost)
            for name in ("structural", "native_risk")
        ]
        changes.append(
            {
                "cost_bps_per_side": cost,
                "net_return_delta_pp_risk_minus_structural": b["net_return_pct"]
                - a["net_return_pct"],
                "drawdown_delta_pp_risk_minus_structural": b["maximum_5m_mark_drawdown_pct"]
                - a["maximum_5m_mark_drawdown_pct"],
            }
        )
        rankings[str(cost)] = [
            r["policy"]
            for r in sorted([a, b], key=lambda r: r["net_return_pct"], reverse=True)
            if feasible[r["policy"]]
        ]
    dominates = feasible["native_risk"] and all(
        r["net_return_delta_pp_risk_minus_structural"] > 0
        and r["drawdown_delta_pp_risk_minus_structural"] <= 0
        for r in changes
    )
    selection = "native_risk" if dominates else "structural" if feasible["structural"] else None
    decision = {
        "feasible_at_both_costs": feasible,
        "risk_arm_dominates_at_both_costs": dominates,
        "policy_preferred_under_frozen_rule": selection,
        "rankings_by_cost_with_both_cost_feasibility": rankings,
        "automatic_paper_or_live_promotion": False,
        "production_eligible": False,
        "orders_enabled": False,
    }
    result = {
        "accounts": rows,
        "risk_minus_structural": changes,
        "decision": decision,
        "patterns": patterns(),
        "auxiliary_author_reference": auxiliary_reference(),
        "source_audit": ref(HERE / "source-audit.json"),
        "causal_clock_audit": ref(HERE / "causal-clock-audit.json"),
        "contract_identity_calendar_audit": ref(HERE / "identity-calendar-audit.json"),
        "preserved_disk_guard_attempt_and_recovery": ref(HERE / "disk-recovery.json"),
        "parity": ref(HERE / "parity-result.json"),
        "protocol": ref(HERE / "PROTOCOL.md"),
        "returns_cumulative_not_annualized": True,
        "independent_out_of_sample": False,
        "full_point_in_time_universe": False,
        "orders_enabled": False,
    }
    save(HERE / "comparison.json", result)
    save(HERE / "selected-policy.json", decision)
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
        HERE / "comparison.csv", index=False
    )
    lines = [
        "# 黑名单规则替代：原生回测结果",
        "",
        "区间：2026-01-01 至 2026-11-09 05:00 UTC。初始1,000虚拟USDT、3倍逐仓、最多12仓、每日80币。保留FlatTopBoost的原NFI X8信号与加减仓，仅研究币池准入。费用为每边15/8bps，含原生资金费；收益为累计净收益，回撤采用含未平仓浮亏及剩余平仓费的完整5分钟净值。",
        "",
        "| 规则 | 每边成本bps | 净收益 | 最大回撤 | 自然平仓 | 加仓成交 | 移出币池后加仓 | 准入 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['policy']} | {r['cost_bps_per_side']} | {r['net_return_pct']:.2f}% | {r['maximum_5m_mark_drawdown_pct']:.2f}% | {r['natural_completed_trades']} | {r['actual_addition_fills']} | {r['held_addition_fills_outside_current_pool']} | {'PASS' if r['feasibility_passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## 冻结规则与结论",
        "",
        "structural：只保留7条产品类型排除规则；至少60个已完成日线、前一日成交额为正，按前一完整UTC日成交额选前80。删除永久的币名、板块和历史事件惩罚。",
        "",
        "native_risk：上述规则后使用原生RangeStabilityFilter(10,0.01,0.99)和VolatilityFilter(10,0.05,0.50)。实际取11根已完成日线，次日重新判断，达标自动恢复。波动阈值是原生滚动指标，不能解释为日涨跌幅5%-50%。",
        "",
        "```json",
        json.dumps(decision, ensure_ascii=False, indent=2),
        "```",
        "",
        "风控筛选不会自动平掉原有仓位，也不会阻止原NFI加仓；上表单列移出后加仓。涨跌幅和波动过滤不能提前识别黑客事件、退市或项目风险。样本内观察到的币种收益只用于诊断，不能据此重新增删名字。",
        "",
        "事件禁入记录需要合约身份、原因、证据、生效/可知时间和解除条件。对供应完整性破坏、黑客攻击等事件，价格窗口过期不能作为自动解除依据。本轮未重建历史公告发布时钟，研究方案不具备完整事件风控替代能力。",
        "",
        "## 数据与名单规律",
        "",
        "同一343候选集合中，2025年末名单命中66币，原冻结版本105币，最新官方版本107币。期间既有加入也有删除，不能把名单回填到年初当作当时已知。原105币中60币已不满足当前市场/档位条件；45币仍可研究，其中USTC保留产品类型排除，实际恢复44个名字。",
        "",
        "LIT存在旧Litentry与新Lighter合约的同名复用。新合约日线和5分钟数据均按实际上市时钟截断，避免归档中上市前静止价格进入预热；267合约身份时钟核查零违规。身份须含市场、合约及上市时间，不能只存ticker。",
        "",
        "风险过滤的全313日诊断：区间上限排除1,641个币日；波动下限排除3,724个币日，波动上限仅93个币日。BTC因此被排除128天、ETH51天、BNB125天。币池换入次数由2,768增至3,270（均不含初始80币），增加约18.1%；这不等同于真实订单换手。官方示例参数存在排除低波动大币的明显作用，不宜简单称作危险币过滤器。",
        "",
        "## 证据边界",
        "",
        "实际原生引擎负责指标、成交、钱包、资金费、DCA和退出；历史币池/时钟桥接及无损缓存是本地扩展，不能称为上游原生历史过滤支持。固定80币控制的89,917净值点和244笔原生交易记录完全一致。每个新账户验证实际退出码、Decimal逐单现金、全日历净值、实际币池与初始准入轨迹。",
        "",
        "native_risk首轮触发1.5GiB磁盘余量保护（实际退出码73），该尝试未完成，不计作策略收益结果。失败日志和资源记录保留。仅清理带逐文件哈希清单的生成缓存；已结束的旧现货回测全部非缓存证据哈希保持不变。按相同源码、配置、参数和数据计划补跑，没有放宽资源或经济门槛。",
        "",
        "本研究删除了新方案中的事后币名黑名单，但仍固定当前作者候选、市场状态/杠杆档位及后出的策略版本，存在幸存者和来源选择偏差；并非完整历史时点币池或独立样本外验证。未重建历史事件公告流。旧黑名单高收益账户仅为回顾性辅助参考，不用于证明无偏增益。没有修改运行服务、下单或发送新通知。",
        "",
    ]
    lines += [
        "## 旧作者名单：仅回顾性辅助参考",
        "",
        "| 每边成本bps | 净收益 | 最大回撤 | 加仓成交 |",
        "|---:|---:|---:|---:|",
    ]
    for r in result["auxiliary_author_reference"]["accounts"]:
        lines.append(
            f"| {r['cost_bps_per_side']} | {r['net_return_pct']:.2f}% | {r['maximum_5m_mark_drawdown_pct']:.2f}% | {r['actual_addition_fills']} |"
        )
    lines += [
        "",
        "上述账户沿用后来冻结的币名/事件黑名单，不能当成年初已知的无偏控制组，也不能凭收益高就恢复事后封禁。",
        "",
    ]
    (HERE / "REPORT.md").write_text("\n".join(lines))
    save(
        HERE / "verification.json",
        {
            "passed": True,
            "completed_native_accounts": len(rows),
            "five_minute_points_per_account": [r["valuation_points"] for r in rows],
            "comparison": ref(HERE / "comparison.json"),
            "report": ref(HERE / "REPORT.md"),
            "analyzer": ref(HERE / "analyze.py"),
            "orders_enabled": False,
        },
    )
    print(json.dumps({"decision": decision, "risk_minus_structural": changes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
