"""Audit actual native accounts and add independently funded sleeves at identical clocks."""

import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    audit_trades,
    save,
    sha,
)


HERE = Path(__file__).resolve().parent
PLAN = HERE / "v003/plan.json"


def metrics(nav):
    values = [float(x) for x in nav]
    if not values or any(not np.isfinite(x) or x <= 0 for x in values):
        raise ValueError("Invalid full-account NAV")
    high = values[0]
    worst = 0.0
    for value in values:
        high = max(high, value)
        worst = max(worst, (high - value) / high)
    return {
        "net_return": values[-1] / values[0] - 1,
        "maximum_hourly_mark_drawdown": worst,
        "initial_usdt": values[0],
        "ending_usdt": values[-1],
        "observations": len(values),
    }


def combine(left, right):
    if not left.index.equals(right.index) or left.index.has_duplicates:
        raise ValueError("Sleeve clock mismatch; never forward-fill or silently inner-join")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Missing sleeve NAV")
    if left.iloc[0] != 500 or right.iloc[0] != 500:
        raise ValueError("Combination requires two actual500-USDT wallets")
    return left + right


def read_native(path, strategy):
    archives = list((path / "native_results").glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("Exactly one actual native archive required")
    with ZipFile(archives[0]) as archive:
        names = [
            n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        ]
        stats = json.loads(archive.read(names[0]))["strategy"][strategy]
        config_name = next(n for n in archive.namelist() if n.endswith("_config.json"))
        config = json.loads(archive.read(config_name))
    return stats, archives[0], config


def main():  # noqa: C901 - one sealed account matrix and its independent artifact checks
    plan = json.loads(PLAN.read_text())
    out = Path(plan["output_root"])
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    accounts, summaries, evidence = {}, [], []
    for cost in plan["total_cost_bps_per_side"]:
        for candidate in plan["candidates"]:
            key = f"{candidate['name']}_{cost}bps"
            path = out / key
            receipt = json.loads((path / "process_receipt.json").read_text())
            if receipt["returncode"] != 0 or receipt["result_sha256"] != sha(path / "result.json"):
                raise ValueError("Incomplete or altered account")
            result = json.loads((path / "result.json").read_text())
            stats, archive, archived_config = read_native(path, candidate["strategy"])
            audit = audit_trades(stats)
            save(path / "independent_archive_cash_recheck.json", audit)
            if (
                stats["dry_run_wallet"] != candidate["capital"]
                or archived_config["fee"] != cost / 10000
            ):
                raise ValueError("Native archive capital/cost differs")
            if stats["max_open_trades"] != 6 or stats["trading_mode"] != "futures":
                raise ValueError("Native archive contract differs")
            if any(t["leverage"] != 1 or t["is_short"] for t in stats["trades"]):
                raise ValueError("Unexpected direction or leverage")
            frame = pd.read_parquet(path / "hourly_equity.parquet")
            nav = frame.set_index("date").hourly_mark_equity
            nav = nav.loc[plan["start"] : plan["comparison_end"]]
            if not nav.index.as_unit("ns").equals(expected):
                raise ValueError("Incomplete/duplicate full account calendar")
            if nav.iloc[0] != candidate["capital"]:
                raise ValueError("Unexpected starting equity")
            full = metrics(nav)
            native_full = result["periods"][0]
            if abs(full["net_return"] - native_full["net_return"]) > 1e-12:
                raise ValueError("Independent endpoint return mismatch")
            if (
                abs(full["maximum_hourly_mark_drawdown"] - native_full["hourly_mark_drawdown"])
                > 1e-12
            ):
                raise ValueError("Independent peak-walk DD mismatch")
            accounts[key] = nav
            summaries.append(
                {
                    "account": candidate["name"],
                    "cost_bps_per_side": cost,
                    "natural_closes": sum(
                        t["exit_reason"] != "force_exit"
                        and t["close_timestamp"]
                        < pd.Timestamp(plan["comparison_end"]).timestamp() * 1000
                        for t in stats["trades"]
                    ),
                    "native_terminal_return_separate": stats["profit_total"],
                    **full,
                }
            )
            evidence.append(
                {
                    "account": key,
                    "result": {
                        "path": str(path / "result.json"),
                        "sha256": sha(path / "result.json"),
                    },
                    "native_archive": {"path": str(archive), "sha256": sha(archive)},
                    "nav": {
                        "path": str(path / "hourly_equity.parquet"),
                        "sha256": sha(path / "hourly_equity.parquet"),
                    },
                    "cash_recheck": audit["passed"],
                    "native_exit_code": receipt["returncode"],
                }
            )
    combined = []
    for cost in plan["total_cost_bps_per_side"]:
        nav = combine(
            accounts[f"Bandtastic_500_{cost}bps"], accounts[f"AverageStrategy_500_{cost}bps"]
        )
        nav.rename("equity").to_frame().to_parquet(out / f"combined_{cost}bps.parquet")
        parts = {p["name"]: metrics(nav.loc[p["start"] : p["end"]]) for p in plan["periods"]}
        components = [
            r
            for r in summaries
            if r["cost_bps_per_side"] == cost and r["account"].endswith("_1000")
        ]
        value = metrics(nav)
        verdict = (
            value["net_return"] > 0
            and value["maximum_hourly_mark_drawdown"] <= 0.5
            and all(value["net_return"] > r["net_return"] for r in components)
            and all(part["net_return"] > 0 for part in parts.values())
        )
        daily_left = (
            accounts[f"Bandtastic_500_{cost}bps"].resample("1D").last().pct_change().dropna()
        )
        daily_right = (
            accounts[f"AverageStrategy_500_{cost}bps"].resample("1D").last().pct_change().dropna()
        )
        row = {
            "account": "two_actual_500_wallets",
            "cost_bps_per_side": cost,
            **value,
            "periods": parts,
            "daily_sleeve_return_correlation": float(daily_left.corr(daily_right)),
            "passes_frozen_cost_gate": verdict,
        }
        combined.append(row)
    parity_out = Path(json.loads((HERE / "v002/plan.json").read_text())["output_root"])
    old = parity_out / "Bandtastic_1000_15bps"
    new = out / "Bandtastic_1000_15bps"
    old_stats, _, _ = read_native(old, "Bandtastic")
    new_stats, _, _ = read_native(new, "Bandtastic")
    if (
        old_stats["trades"] != new_stats["trades"]
        or old_stats["final_balance"] != new_stats["final_balance"]
    ):
        raise ValueError("Observation revision changed native trades or cash")
    legacy = (
        pd.read_parquet(new / "legacy_main_clock_equity.parquet")
        .set_index("date")
        .hourly_mark_equity
    )
    original = pd.read_parquet(old / "hourly_equity.parquet").set_index("date").hourly_mark_equity
    # v002 adds the known initial cash row after observing; legacy raw capture excludes it.
    if not original.loc[legacy.index].equals(legacy):
        raise ValueError("Legacy observation parity failed")
    save(
        HERE / "v003/observation_parity.json",
        {
            "passed": True,
            "native_trades": len(old_stats["trades"]),
            "legacy_nav_points": len(legacy),
            "financial_state_changed": False,
            "new_clock_is_distinct_from_legacy": True,
        },
    )
    result = {
        "native_accounts": summaries,
        "combinations": combined,
        "evidence": evidence,
        "combination_passes_both_costs": all(r["passes_frozen_cost_gate"] for r in combined),
        "new_effective_factors_proven": 0,
        "independent_validation": False,
        "orders_enabled": False,
        "production_eligible": False,
        "window_utc": [plan["start"], plan["comparison_end"]],
        "initial_combination_usdt": 1000,
    }
    save(HERE / "v003/comparison.json", result)
    pd.DataFrame(
        summaries + [{k: v for k, v in r.items() if k != "periods"} for r in combined]
    ).to_csv(HERE / "v003/comparison.csv", index=False)
    lines = [
        "# 布林均值回归与EMA趋势：完整原生账户结果",
        "",
        "2025-01-01 00:00至2026-08-31 23:00 UTC；40币、1×、每个钱包最多6仓。",
        "收益为含浮盈亏、原生资金费及退出费预留的完整逐小时mark净值累计变化，不是年化或盘中最坏回撤。",
        "",
        "| 账户 | 每边成本bps | 累计收益 | 逐小时最大回撤 | 期末USDT |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summaries + combined:
        lines.append(
            f"|{row['account']}|{row['cost_bps_per_side']}|"
            f"{row['net_return']:.2%}|{row['maximum_hourly_mark_drawdown']:.2%}|"
            f"{row['ending_usdt']:.4f}|"
        )
    lines += [
        "",
        (
            "组合严格相加两个实际500 USDT原生钱包的同时刻净值，合计本金1000；"
            "钱包不互转资金、不再平衡，合计最多12仓，并非共享钱包。"
        ),
        (
            f"冻结双成本组合门槛通过：{result['combination_passes_both_costs']}。"
            "结果未证明新有效因子或独立样本外收益，持续更高收益目标尚未完成。"
        ),
        "",
        (
            "所有8账户进程成功、完整14592个小时日历、原生ZIP和Decimal现金重核均已实际检查。"
            "v003只读观察修订与v002控制的全部原生成交、期末现金及旧观察曲线完全一致。"
        ),
        "",
        (
            "固定存续币池、现时规则、已观察历史与源码旧优化均有偏差。"
            "Bandtastic和AverageStrategy均属既有机制的源码复现，不能算新独立alpha；"
            "Average原版startup0递归稳定性仍未认证。没有部署或订单。"
        ),
        "",
        "完整分段、相关性、原生强平另列和逐账户证据见comparison.json；失败与修订见上层README.md。",
    ]
    (HERE / "v003/REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
