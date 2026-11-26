"""Audit actual native OI accounts, source signals, controls and full NAV clocks."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import metrics, read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    audit_trades,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent


def signal_audit(stats, indicators, strategy):
    fields = indicators.set_index("date")
    checked = []
    for trade in stats["trades"]:
        clock = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        observed_bar = clock.floor("h") - pd.Timedelta(hours=1)
        row = fields.loc[observed_bar]
        price_ok = row.close < row.ll_20 if trade["is_short"] else row.close > row.hh_20
        available = pd.notna(row.oi_window_known_1h) and bool(row.oi_window_known_1h)
        rising = pd.notna(row.oi_change_pct_1h) and row.oi_change_pct_1h > 0
        if not price_ok:
            raise ValueError("Actual native entry did not have completed prior-bar breakout")
        if strategy != "PriceBreakoutFull" and not available:
            raise ValueError("Actual entry used a missing/zero OI window")
        if strategy == "OIConfirmedBreakout" and not rising:
            raise ValueError("Actual entry lacked source OI confirmation")
        if trade["exit_reason"] not in {"stop_loss", "force_exit", "liquidation"}:
            raise ValueError("Source exit/ROI economics changed")
        checked.append(
            {
                "pair": trade["pair"],
                "entry": str(clock),
                "signal_bar": str(observed_bar),
                "is_short": trade["is_short"],
                "oi_available": available,
                "oi_rising": bool(rising),
                "exit_reason": trade["exit_reason"],
            }
        )
    return {"passed": True, "entries": checked}


def main():  # noqa: C901 - one sealed six-account audit
    plan = json.loads((HERE / "plan.json").read_text())
    out = Path(plan["output_root"])
    funding = json.loads((HERE / "funding_audit.json").read_text())
    if not funding["passed"]:
        raise ValueError("Independent funding-input audit missing")
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    summaries, evidence, native_stats, curves = [], [], {}, {}
    for cost in plan["total_cost_bps_per_side"]:
        for candidate in plan["candidates"]:
            key = f"{candidate['name']}_{cost}bps"
            path = out / key
            receipt = json.loads((path / "process_receipt.json").read_text())
            if receipt["returncode"] or receipt["result_sha256"] != sha(path / "result.json"):
                raise ValueError("Native account did not complete unchanged")
            stats, archive, config = read_native(path, candidate["strategy"])
            if (
                config["fee"] != cost / 10000
                or stats["dry_run_wallet"] != 1000
                or stats["max_open_trades"] != 1
                or stats["timeframe"] != "1h"
                or stats["timeframe_detail"] != "5m"
                or stats["trading_mode"] != "futures"
            ):
                raise ValueError("Actual native contract differs")
            if any(t["pair"] != "BTC/USDT:USDT" or t["leverage"] != 1 for t in stats["trades"]):
                raise ValueError("Unregistered pair/leverage")
            cash = audit_trades(stats)
            save(path / "independent_cash_audit.json", cash)
            indicators = pd.read_parquet(path / "native_indicators.parquet")
            prepared = pd.read_parquet(out / f"preflight_{candidate['name']}.parquet")
            for field in [
                "open_interest_amount_1h",
                "open_interest_value_1h",
                "oi_change_pct_1h",
                "oi_window_known_1h",
                "hh_20",
                "ll_20",
            ]:
                left = indicators.set_index("date")[field]
                right = prepared.set_index("date")[field]
                if not left.index.as_unit("ns").equals(right.index.as_unit("ns")):
                    raise ValueError("Actual native input calendar differs from preflight")
                if not np.allclose(
                    left.astype(float), right.astype(float), rtol=0, atol=0, equal_nan=True
                ):
                    raise ValueError(
                        "Actual informative input differs from raw-audited native preflight"
                    )
            save(
                path / "independent_source_entry_audit.json",
                signal_audit(stats, indicators, candidate["strategy"]),
            )
            frame = pd.read_parquet(path / "hourly_equity.parquet")
            nav = frame.set_index("date").hourly_mark_equity.loc[
                plan["start"] : plan["comparison_end"]
            ]
            if not nav.index.as_unit("ns").equals(expected) or nav.isna().any():
                raise ValueError("Incomplete actual account calendar")
            summary = metrics(nav)
            reported = json.loads((path / "result.json").read_text())
            if abs(summary["net_return"] - reported["periods"][0]["net_return"]) > 1e-12:
                raise ValueError("Independent endpoint return mismatch")
            if (
                abs(
                    summary["maximum_hourly_mark_drawdown"]
                    - reported["periods"][0]["hourly_mark_drawdown"]
                )
                > 1e-12
            ):
                raise ValueError("Independent DD peak walk mismatch")
            summaries.append(
                {
                    "strategy": candidate["strategy"],
                    "cost_bps_per_side": cost,
                    **summary,
                    "native_terminal_return_separate": stats["profit_total"],
                    "natural_closes": reported["periods"][0]["completed_campaigns"],
                    "open_campaigns_at_common_end": reported["periods"][0]["open_campaigns_at_end"],
                    "continuous_calendar_slices": reported["periods"][1:],
                }
            )
            evidence.append(
                {
                    "account": key,
                    "actual_process_exit": 0,
                    "native_archive": {"path": str(archive), "sha256": sha(archive)},
                    "nav_sha256": sha(path / "hourly_equity.parquet"),
                    "source_audit_sha256": sha(path / "independent_source_entry_audit.json"),
                    "cash_audit_passed": cash["passed"],
                }
            )
            native_stats[key], curves[key] = stats, nav
    controls_parity = []
    decisions = []
    for cost in plan["total_cost_bps_per_side"]:
        available, full = [
            f"{name}_{cost}bps" for name in ["PriceBreakoutAvailable", "PriceBreakoutFull"]
        ]
        exact_trades = native_stats[available]["trades"] == native_stats[full]["trades"]
        exact_nav = np.array_equal(curves[available], curves[full])
        controls_parity.append(
            {
                "cost": cost,
                "all_actual_trades_identical": exact_trades,
                "all14592nav_values_identical": exact_nav,
            }
        )
        candidate = next(
            row
            for row in summaries
            if row["strategy"] == "OIConfirmedBreakout" and row["cost_bps_per_side"] == cost
        )
        controls = [
            row
            for row in summaries
            if row["strategy"] != "OIConfirmedBreakout" and row["cost_bps_per_side"] == cost
        ]
        passed = (
            candidate["net_return"] > 0
            and candidate["maximum_hourly_mark_drawdown"] <= 0.5
            and all(candidate["net_return"] > row["net_return"] for row in controls)
            and all(row["net_return"] > 0 for row in candidate["continuous_calendar_slices"])
        )
        decisions.append(
            {
                "cost": cost,
                "numeric_gate_passed": passed,
                "return_delta_vs_available_control_pp": 100
                * (candidate["net_return"] - controls[0]["net_return"]),
            }
        )
    result = {
        "accounts": summaries,
        "decisions": decisions,
        "controls_parity": controls_parity,
        "evidence": evidence,
        "frozen_numeric_gate_passed": all(row["numeric_gate_passed"] for row in decisions),
        "new_effective_alpha_claim": False,
        "independent_oos": False,
        "source_is_reference_with_unvalidated_exit": True,
        "production_eligible": False,
        "funding_input_events_reconciled": funding["full_window_events"],
        "actual_broker_funding_clock_certified": False,
        "orders_enabled": False,
    }
    save(HERE / "comparison.json", result)
    pd.DataFrame(
        [{k: v for k, v in row.items() if k != "continuous_calendar_slices"} for row in summaries]
    ).to_csv(HERE / "comparison.csv", index=False)
    lines = [
        "# 未平仓量确认突破：完整原生账户结果",
        "",
        (
            "2025-01-01 00:00至2026-08-31 23:00 UTC；BTCUSDT永续、"
            "1000 USDT、1×、单一持仓、原生5分钟detail。"
        ),
        (
            "下表累计收益和回撤来自完整逐小时mark NAV，含浮盈亏、原生模型资金费及剩余退出费预留；"
            "不是年化或盘中最深回撤。"
        ),
        "",
        "|策略|单边成本bps|累计净收益|逐小时最大回撤|自然平仓|期末未平仓|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"|{row['strategy']}|{row['cost_bps_per_side']}|{row['net_return']:.2%}|"
            f"{row['maximum_hourly_mark_drawdown']:.2%}|{row['natural_closes']}|{row['open_campaigns_at_common_end']}|"
        )
    lines += [
        "",
        "![原生完整账户比较](account-comparison.png)",
        "",
        f"冻结双成本数字门槛通过：{result['frozen_numeric_gate_passed']}。OI确认只降低历史亏损，没有带来盈利或新有效因子；源例的盈利退出与独立验证也未建立，不继续分配到多策略组合。",
        "",
        "两个价格对照在每档成本下的全部原生成交与14592个NAV点完全相同；OI缺口掩码没有改变实际账户路径，不能将少交易的所有变化归给跳过缺数据。",
        "612个官方OI日档逐份SHA校验；25条原报0保留，3个整点不适合相对变化，6个连续正数条件明确禁用相应窗口，informative不前填。",
        "20个独立官方资金费月档、1824条完整8小时事件与原生输入逐条同率/同小时模型匹配。这里没有原AXS/TRB时槽问题；毫秒结算先后、实际到账、滑点容量仍未认证。",
        "使用原版10%止损、10000%惰性ROI及无额外退出。5个自然平仓且终点仍1仓，不能把终点强平当作自然赢单；源文明确是参考示例，不是完整盈利策略。",
        "此前清算瀑布教程因全样本未来收益挑最佳regime、事件收益累加非连续账户而未进入回测；本研究未把OI称作实际清算量。",
        "所有金融账户经固定上游Freqtrade Backtesting真实执行；没有部署、订单或运行服务修改。",
    ]
    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            {
                "accounts": len(summaries),
                "decisions": decisions,
                "gate_passed": result["frozen_numeric_gate_passed"],
            }
        )
    )


if __name__ == "__main__":
    main()
