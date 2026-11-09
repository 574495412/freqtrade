# ruff: noqa: RUF001 - Chinese report typography
"""Render only completed native evidence into a durable review."""

from pathlib import Path

import pandas as pd

from research.crypto.nfi_blacklist_rules_20261109.inputs import read


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001/accounts"


def main():
    rows = read(HERE / "cap100-comparison.json")["accounts"]
    lines = [
        "# NFI X8 真实回测核对",
        "",
        "研究锁按用户指令解除；解除不代表已排除过拟合。",
        "",
        (
            "窗口：2026-01-01 00:00 至2026-11-09 05:00 UTC。每账户初始100 USDT、"
            "可用保证金/现货投入预算99 USDT；利润不扩大99 USDT仓位预算。"
            "合约3倍、12仓；现货1倍、6仓。收益包含未平仓浮盈亏、双边成本及原生合约资金费。"
        ),
        "",
        (
            "| 原生账户 | 单边成本 | 期末标记净值 | 净收益 | 5分钟最大回撤 | 自然平仓胜率 | "
            "截止日浮盈亏 | 加仓成交 | 最高已用保证金/本金 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "author100": "合约：事后作者黑名单",
        "structural100": "合约：不排除具体币名",
        "spot100": "现货：固定36币",
    }
    for r in rows:
        win = (
            f"{r['natural_win_rate_pct']:.2f}%"
            if r["natural_win_rate_pct"] is not None
            else "无样本"
        )
        lines.append(
            f"| {labels[r['name']]} | {r['cost_bps_each_side']}bps | "
            f"{r['ending_marked_USDT']:.2f}U | {r['net_return_pct']:.2f}% | "
            f"{r['maximum_5m_mark_drawdown_pct']:.2f}% | "
            f"{win} ({r['natural_wins']}/"
            f"{r['natural_completed_campaigns']}) | {r['open_mark_pnl_USDT']:.2f}U | "
            f"{r['addition_fills']} | {r['peak_open_margin_cost_USDT']:.2f}U |"
        )
    lines += [
        "",
        (
            "每组实际退出码均为0，原生成交按Decimal复算资金流水，89,917个共同净值时点完整。"
            "交易杠杆、最大仓位、99U资金上限及初次入场币池均核对通过。"
            "表中胜率只含自然结束的交易周期；未平仓损益已计入收益和回撤。"
        ),
        "",
        (
            "原201.24%来自1,000U、可随盈利扩大仓位的历史账户，与本次固定100U合同不相同。"
            "其99.67%自然平仓胜率没有包括截止日4笔未平仓亏损；这些浮亏约679.35U已计入净值，"
            "并非从收益中漏掉。包含窗口末强制平仓后，胜率为98.39%。"
        ),
        "",
        (
            "此前现货与合约共6个退出时点前视警告，已经原生单币/双币×干净/旧缓存共24组对照"
            "定位为诊断回放缓存串用。隔离每次回放目录后，原官方诊断范围内警告均为0；"
            "完整回放成交文件逐字节不变。详见[诊断证据](DIAGNOSTIC.md)。"
        ),
        "",
        (
            "仍不能证明没有过拟合：作者黑名单属于事后信息；去掉具体币名后仍未补齐退市币和完整"
            "历史市场规则；相关2026年结果已反复用于选策略，不能称独立样本外。"
            "本轮只重现8bps合约与13bps现货合同，更高成本下的100U结果未验证。"
            "5分钟标记回撤不是盘中最坏损失，也不保证实盘最大亏损。"
            "资金费已计入原生交易和净值，但模拟钱包不等同交易所逐时结算余额，"
            "账户结算、清算及实际成交仍未认证一致。"
        ),
        "",
        (
            "这三组均是固定版本Freqtrade原生撮合与持仓结果，使用明确记录的本地历史选币/时钟、"
            "只读估值、无损存储和资金上限扩展，不是Vibe-Trading SignalEngine或AlphaBench结果。"
            "通过单个历史风险门槛，不等于生产资格或独立验证通过。"
        ),
        "",
        "## 线上观察",
        "",
        (
            "2026-11-10 01:15至01:16北京时间只读检查：现货/合约进程有心跳、本地账本均0订单和0成交；"
            "合约出现新的418限流，不能称交易链路健康。收益回测已在禁止外连的独立进程完成；"
            "限流原因仍需按实际调用方定位。解除研究锁未处理限流，也没有认证实盘成交一致性。"
        ),
        "",
        "## 月度净收益",
        "",
        "| 月份 | 合约作者黑名单 | 合约无具体币名排除 | 现货36币 |",
        "|---|---:|---:|---:|",
    ]
    monthly = {}
    for r in rows:
        folder = OUT / r["output_key"]
        curve = pd.read_parquet(folder / "five_minute_equity.parquet").set_index("date")
        records = []
        for start, part in curve.groupby(pd.Grouper(freq="MS")):
            if part.empty:
                continue
            end = min(start + pd.offsets.MonthBegin(1), curve.index[-1])
            values = curve.loc[start:end].marked_equity
            gain = (values.iloc[-1] / values.iloc[0] - 1) * 100
            records.append(
                {
                    "month": str(start.date()),
                    "return_pct": float(gain),
                    "local_drawdown_pct": float((1 - values / values.cummax()).max() * 100),
                }
            )
            monthly.setdefault(str(start.date()), {})[r["name"]] = gain
        pd.DataFrame(records).to_csv(folder / "monthly.csv", index=False)
    for month, results in monthly.items():
        cells = [f"{results[r['name']]:.2f}%" for r in rows]
        lines.append("| " + month + " | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 可核查文件",
        "",
        "- [新账户完整指标及源文件哈希](cap100-comparison.json)",
        "- [旧账户完整流水核对](ledger-audit.json)",
        "- [诊断前后成交相等证明](cache-proof.json)",
        "- [原始数据追加证明](spot-prefix-proof.json)",
        f"- 本地原生结果、逐笔成交、完整净值：`{OUT}`。",
        "",
        (
            "官方方法边界：[回测成交假设](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting)、"
            "[前视诊断及其合成账户设置](https://www.freqtrade.io/en/stable/lookahead-analysis/)。"
        ),
        "",
    ]
    (HERE / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
