"""Actual native results, full hourly calendars and source allocation attribution."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics, read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    audit_trades,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
PLAN = HERE / "v002/plan.json"


def allocation_audit(stats, trace, scores, strategy):
    field = "skew360" if strategy == "AnnualSkew" else "volatility63"
    accepted = {
        pd.Timestamp(row["clock"]).strftime("%Y-%m") + "|" + row["pair"]: row
        for row in trace["accepted_intents"]
    }
    checks = []
    months = {}
    for trade in stats["trades"]:
        opened = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        month = opened.strftime("%Y-%m")
        key = month + "|" + trade["pair"]
        if key not in accepted:
            raise ValueError("Native fill without a source-accepted intent")
        intent = accepted[key]
        side = -1 if trade["is_short"] else 1
        if scores["months"][month][field][trade["pair"]] != side:
            raise ValueError("Native direction differs from frozen monthly source ranking")
        entries = [order for order in trade["orders"] if order["ft_is_entry"]]
        if len(entries) != 1:
            raise ValueError("Unregistered scale-in or entry decomposition")
        notional = sum(order["amount"] * order["safe_price"] for order in entries)
        if notional > intent["maximum_budget_usdt"] + 0.01:
            raise ValueError("Native minimum order upsized the source budget")
        if opened.day != 2:
            raise ValueError("Unexpected monthly entry calendar")
        if trade["exit_reason"] == "exit_signal":
            closed = pd.to_datetime(trade["close_timestamp"], unit="ms", utc=True)
            if closed.day != 1:
                raise ValueError("Source monthly exit occurred on a different day")
        elif trade["exit_reason"] not in {"force_exit", "stop_loss", "liquidation"}:
            raise ValueError("Unregistered risk/ROI exit policy")
        months[month] = months.get(month, 0) + 1
        if months[month] > 20:
            raise ValueError("More than one20-position monthly batch")
        checks.append(
            {
                "pair": trade["pair"],
                "month": month,
                "is_short": trade["is_short"],
                "entry_notional_usdt": notional,
                "frozen_budget_usdt": intent["maximum_budget_usdt"],
                "exit_reason": trade["exit_reason"],
            }
        )
    return {
        "passed": True,
        "actual_fills": checks,
        "filled_monthly_campaign_counts": months,
        "minimum_order_rejections": [
            row for row in trace["attempts"].values() if row["reason"] == "MINIMUM_EXCEEDS_BUDGET"
        ],
        "accepted_but_not_filled": len(accepted) - len(checks),
    }


def funding_limit_exposures(stats, review):
    exposures = []
    for gap in review["expected_unresolved_slots"]:
        clock = pd.Timestamp(gap["expected_slot"]).timestamp() * 1000
        for trade in stats["trades"]:
            if (
                trade["pair"] == gap["pair"]
                and trade["open_timestamp"] < clock < trade["close_timestamp"]
            ):
                exposures.append(
                    {
                        "pair": trade["pair"],
                        "expected_slot": gap["expected_slot"],
                        "is_short": trade["is_short"],
                        "open_date": trade["open_date"],
                        "close_date": trade["close_date"],
                        "status": gap["status"],
                        "missing_rate_assumed_zero": False,
                    }
                )
    return exposures


def monthly_budget_audit(trace, frame):
    checks = []
    values = frame.set_index("date")
    for month, actual in trace["monthly_budgets"].items():
        clock = pd.Timestamp(month + "-02T00:00Z")
        row = values.loc[clock]
        if row.open_trades != 0:
            raise ValueError("Monthly capital freeze still has old funded positions")
        expected = float(row.hourly_mark_equity) * 0.99 / 20
        if abs(actual - expected) > 1e-6:
            raise ValueError("Source allocation budget differs from independently marked cash")
        checks.append(
            {
                "month": month,
                "actual_budget_usdt": actual,
                "expected_cash_based_budget_usdt": expected,
                "cash_before_native_first_entry": float(row.hourly_mark_equity),
            }
        )
    return {"passed": True, "months": checks}


def main():  # noqa: C901 - one frozen native matrix audit
    plan = json.loads(PLAN.read_text())
    out = Path(plan["output_root"])
    config = json.loads(Path(plan["candidates"][0]["config"]["path"]).read_text())
    scores = json.loads(Path(config["research_monthly_scores"]).read_text())
    funding_review = json.loads((HERE / "funding_gap_review/review.json").read_text())
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    summaries, evidence, accounts = [], [], {}
    for cost in plan["total_cost_bps_per_side"]:
        for candidate in plan["candidates"]:
            key = f"{candidate['name']}_{cost}bps"
            path = out / key
            receipt = json.loads((path / "process_receipt.json").read_text())
            if receipt["returncode"] or sha(path / "result.json") != receipt["result_sha256"]:
                raise ValueError("Incomplete/altered actual native result")
            stats, archive, actual_config = read_native(path, candidate["strategy"])
            if (
                actual_config["fee"] != cost / 10000
                or stats["dry_run_wallet"] != candidate["capital"]
                or stats["max_open_trades"] != 20
                or stats["timeframe"] != "1d"
                or stats["timeframe_detail"] != "5m"
                or stats["trading_mode"] != "futures"
            ):
                raise ValueError("Actual native account differs from frozen contract")
            if any(trade["leverage"] != 1 for trade in stats["trades"]):
                raise ValueError("Unexpected native leverage")
            trace = json.loads((path / "strategy_allocation_trace.json").read_text())
            allocation = allocation_audit(stats, trace, scores, candidate["strategy"])
            funding_exposures = funding_limit_exposures(stats, funding_review)
            save(
                path / "funding_input_limits.json",
                {
                    "held_unresolved_expected_slots": funding_exposures,
                    "scope": (
                        "Observed-event native funding model, not certified full settlement cash."
                    ),
                    "full_settlement_economics": "UNKNOWN"
                    if funding_exposures
                    else "NOT_CERTIFIED",
                },
            )
            save(path / "independent_allocation_audit.json", allocation)
            cash = audit_trades(stats)
            save(path / "independent_archive_cash_audit.json", cash)
            frame = pd.read_parquet(path / "hourly_equity.parquet")
            budget_checks = monthly_budget_audit(trace, frame)
            save(path / "independent_monthly_budget_audit.json", budget_checks)
            nav = frame.set_index("date").hourly_mark_equity.loc[
                plan["start"] : plan["comparison_end"]
            ]
            if not nav.index.as_unit("ns").equals(expected) or nav.isna().any():
                raise ValueError("Incomplete source account clock")
            if nav.iloc[0] != candidate["capital"]:
                raise ValueError("Wrong source initial equity")
            full = metrics(nav)
            result = json.loads((path / "result.json").read_text())["periods"][0]
            if (
                abs(full["net_return"] - result["net_return"]) > 1e-12
                or abs(full["maximum_hourly_mark_drawdown"] - result["hourly_mark_drawdown"])
                > 1e-12
            ):
                raise ValueError("Independent result/DD reconciliation failed")
            summaries.append(
                {
                    "account": candidate["name"],
                    "cost_bps_per_side": cost,
                    **full,
                    "actual_long_campaigns": sum(not t["is_short"] for t in stats["trades"]),
                    "actual_short_campaigns": sum(t["is_short"] for t in stats["trades"]),
                    "native_terminal_return_separate": stats["profit_total"],
                    "minimum_order_rejections": len(allocation["minimum_order_rejections"]),
                    "held_unresolved_expected_funding_slots": len(funding_exposures),
                }
            )
            accounts[key] = nav
            evidence.append(
                {
                    "account": key,
                    "archive": {"path": str(archive), "sha256": sha(archive)},
                    "result_sha256": sha(path / "result.json"),
                    "nav_sha256": sha(path / "hourly_equity.parquet"),
                    "allocation_sha256": sha(path / "independent_allocation_audit.json"),
                    "cash_recheck_passed": cash["passed"],
                    "native_process_exit_code": 0,
                }
            )
    combinations = []
    for cost in plan["total_cost_bps_per_side"]:
        left = accounts[f"AnnualSkew_500_{cost}bps"]
        right = accounts[f"LowVolControl_500_{cost}bps"]
        total = combine(left, right)
        total.to_frame("equity").to_parquet(out / f"combined_{cost}bps.parquet")
        full = metrics(total)
        slices = {
            period["name"]: metrics(total.loc[period["start"] : period["end"]])
            for period in plan["periods"]
        }
        controls = [
            row
            for row in summaries
            if row["cost_bps_per_side"] == cost and row["account"].endswith("_1000")
        ]
        gate = (
            full["net_return"] > 0
            and full["maximum_hourly_mark_drawdown"] <= 0.5
            and all(full["net_return"] > row["net_return"] for row in controls)
            and all(row["net_return"] > 0 for row in slices.values())
        )
        correlations = (
            left.resample("1D").last().pct_change().corr(right.resample("1D").last().pct_change())
        )
        combinations.append(
            {
                "account": "two_actual_500_wallets",
                "cost_bps_per_side": cost,
                **full,
                "continuous_calendar_slices": slices,
                "daily_sleeve_nav_return_correlation": float(correlations),
                "passes_frozen_cost_gate": gate,
            }
        )
    first = "AnnualSkew_1000_15bps"
    old_out = Path(json.loads((HERE / "plan.json").read_text())["output_root"])
    old_stats, _, _ = read_native(old_out / first, "AnnualSkew")
    new_stats, _, _ = read_native(out / first, "AnnualSkew")
    if (
        old_stats["trades"] != new_stats["trades"]
        or old_stats["final_balance"] != new_stats["final_balance"]
    ):
        raise ValueError("Initial-cash report fix changed actual financial account")
    old_nav = pd.read_parquet(old_out / first / "hourly_equity.parquet")
    repeated = old_nav.loc[old_nav.date.duplicated(keep=False)]
    if (
        len(repeated) != 2
        or repeated.date.nunique() != 1
        or repeated.date.iloc[0] != pd.Timestamp(plan["start"])
    ):
        raise ValueError("Original failure was not exactly the recorded initial duplicate")
    if repeated.hourly_mark_equity.ne(1000).any() or repeated.open_trades.ne(0).any():
        raise ValueError("Duplicated initial rows contain financial activity")
    unique_old = old_nav.drop_duplicates("date").set_index("date").hourly_mark_equity
    if not np.array_equal(unique_old.to_numpy(), accounts[first].to_numpy()):
        raise ValueError("Corrected full account changed an observed NAV value")
    save(
        HERE / "v002/clock_parity.json",
        {
            "passed": True,
            "actual_native_trades": len(new_stats["trades"]),
            "actual_financial_state_unchanged": True,
            "unique_original_nav_values_all_exact": True,
            "original_duplicate_initial_cash_rows": 2,
        },
    )
    result = {
        "accounts": summaries,
        "combinations": combinations,
        "evidence": evidence,
        "passes_both_costs": all(row["passes_frozen_cost_gate"] for row in combinations),
        "source_strategy_replication": False,
        "independent_oos": False,
        "new_effective_factors_proven": 0,
        "complete_actual_settlement_economics": "UNKNOWN_UNRESOLVED_EXPECTED_FUNDING_SLOTS",
        "qualified_after_input_limits": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "v002/comparison.json", result)
    pd.DataFrame(
        summaries
        + [
            {k: v for k, v in row.items() if k != "continuous_calendar_slices"}
            for row in combinations
        ]
    ).to_csv(HERE / "v002/comparison.csv", index=False)
    lines = [
        "# 年度偏度与低波动：完整原生账户结果",
        "",
        "2025-01-01 00:00至2026-08-31 23:00 UTC；固定40币、1×逐仓，20个单钱包目标仓位。",
        (
            "表中为原生资金费与成本模型下，含浮盈亏、剩余平仓费预留的完整逐小时mark NAV累计收益；"
            "不是年化或盘中最大回撤。"
        ),
        (
            "AXS/TRB在2026-06-24 04:00 UTC的预期4小时时槽仍无公开结算记录或变频公告证明；"
            "已有持仓暴露须保留UNKNOWN，不能把条件模型数字当成已核实的完整实际结算收益。"
        ),
        "",
        "|账户|每边成本bps|累计收益|逐小时最大回撤|期末USDT|",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summaries + combinations:
        lines.append(
            f"|{row['account']}|{row['cost_bps_per_side']}|{row['net_return']:.2%}|"
            f"{row['maximum_hourly_mark_drawdown']:.2%}|{row['ending_usdt']:.4f}|"
        )
    lines += [
        "",
        "![条件资金费模型下的原生账户比较](account-comparison.png)",
        "",
        f"冻结双成本组合门槛通过：{result['passes_both_costs']}。未证明独立样本外、真实资金费到账或新有效因子；持续更高收益目标仍未完成。",
        "",
        "组合相加两个实际500钱包，没有缩放1000钱包曲线，不转账、不再平衡；可能同币同时多空，最多合计40仓，不是共享保证金对冲。",
        "360日偏度只检验源文固定年周期方向；低波动为63日匹配控制。40币USDT永续、月初先退出/次日入场、订单下限拒绝和原生风险机制都区别于原文45币Coinmetrics现货。",
        "800个输入、120组输入前缀/未来扰动、960项信号前缀抽查及独立财务/实际配权核对的范围见相关JSON；抽查不是完整因果认证。",
        "现金起始同一时点重复的v1失败保留。v002重跑所有原生成交与期末现金相同，全部唯一NAV值相同，只修复初始现金补齐边界。",
        "原生终点强平收益独立保存；20月历史含存续币池/现时规则/已观察历史偏差，持有中的名义敞口会漂移。没有部署或订单。",
    ]
    (HERE / "v002/REPORT.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps({"accounts": len(summaries), "combinations": combinations}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
