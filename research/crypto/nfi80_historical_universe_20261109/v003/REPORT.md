# 日更80币池：2026年同窗口回测结论

**本次历史比较支持日更方案：原版和优化版在两档成本下均提高净收益、降低最大回撤，并通过30%历史回撤门槛。** 按该门槛内优先累计收益的目标，日更优化版最高；日更原版回撤更低。

窗口为2026-01-01 00:00至2026-11-09 05:00 UTC（截止北京时间13:00），初始1,000虚拟USDT，USDT永续逐仓3倍、最多12仓。下列均为累计净收益，已计模型单边成本、原生资金费和期末未平仓估值及剩余平仓成本；回撤为完整5分钟标记净值观察，不能代表盘中最坏情况。

| 单边成本 | 策略 | 固定池收益 / 回撤 | UTC日更池收益 / 回撤 |
|---|---|---:|---:|
| 15 bps | 原版信号 | 77.96% / 19.15% | 129.10% / 15.56% |
| 15 bps | Top首仓1.25倍优化版 | 90.71% / 20.92% | 145.56% / 16.83% |
| 8 bps | 原版信号 | 132.99% / 17.58% | 184.48% / 12.63% |
| 8 bps | Top首仓1.25倍优化版 | 149.92% / 20.76% | 201.24% / 15.66% |

优化版相对对应固定池，15bps增收54.85个百分点、回撤降低4.09个百分点；8bps增收51.32个百分点、回撤降低5.11个百分点。7月至截止日，日更优化版在15/8bps下收益为18.69%/42.10%，对应固定池为−2.68%/24.38%。四个新日更账户自然平仓304—308笔，均无模型爆仓；加仓次数见下表，不能把加仓成交当作独立策略样本。

选币每天UTC零点使用上一完整日真实成交额，在冻结作者候选范围、黑名单和市场资格内选80币；初始80币及顺序与固定池完全一致。313个日历日累计覆盖221币、换入2470次。移出池持仓继续由原生NFI管理加减仓和退出，不强制换仓平掉浮亏。

四个新日更账户均由实际原生Freqtrade Backtesting完成，退出码0，原生ZIP、完整89,917个共同净值点、Decimal成交现金核对、逐日实际币池与首仓确认记录全部通过。已有固定池账户复用前再次核对实际策略类、成本、原生现金和完整净值；优化版固定池复跑也逐点、逐笔完全一致。历史输入、PairList和时钟是明确标注的本地扩展，未将其称作上游VolumePairList直接支持的回测。

这仍有当前作者候选、黑名单、市场规则和源版本的回溯/生存偏差，尚非独立样本外验证。UTC日更与此前试验的滚动24小时ticker刷新时点不同；不能将这里的收益直接当作前瞻模拟或实盘收益。15/8bps为固定费用与滑点代理，未模拟逐币订单簿冲击。30%是历史研究准入线，策略没有新增30%账户硬止损。本轮没有新部署，原固定模拟盘未改，误加的日更模拟实验继续停止并禁用。

汇总曾误从混合结果表挑中原版固定基线，已按策略类、原生成交和完整曲线纠正；详见reporting-correction.json，历史错误输出保留，账户计算与冻结假设未变。当前复核入口为v003.final_analysis；comparison.json / comparison.csv和verification.json为对应证据。

---

# Historical daily universe — native backtest validation

2026-01-01 through 2026-11-09 05:00 UTC; 1,000 virtual USDT, 3x isolated, 12 positions. Cumulative net returns, complete 5-minute marked equity including open PnL, close cost and native funding.

| Strategy | Universe | Cost bps/side | Net return | Max drawdown | Natural closes | Add fills | 30% gate |
|---|---|---:|---:|---:|---:|---:|---|
| original | fixed | 15 | 77.96% | 19.15% | 240 | 128 | PASS |
| original | daily | 15 | 129.10% | 15.56% | 304 | 140 | PASS |
| flatboost | fixed | 15 | 90.71% | 20.92% | 240 | 131 | PASS |
| flatboost | daily | 15 | 145.56% | 16.83% | 304 | 142 | PASS |
| original | fixed | 8 | 132.99% | 17.58% | 245 | 49 | PASS |
| original | daily | 8 | 184.48% | 12.63% | 308 | 64 | PASS |
| flatboost | fixed | 8 | 149.92% | 20.76% | 244 | 49 | PASS |
| flatboost | daily | 8 | 201.24% | 15.66% | 306 | 63 | PASS |

{
  "original": {
    "feasible_under_frozen_gate": true,
    "daily_refresh_improved_at_both_costs": true
  },
  "flatboost": {
    "feasible_under_frozen_gate": true,
    "daily_refresh_improved_at_both_costs": true
  }
}

Retrospective author membership, blacklist and metadata remain; no independent OOS/PIT proof. Daily UTC differs from rolling-ticker forward clock.

The native engine has an explicit local historical PairList/clock extension. Its fills, wallet, funding, DCA and exits remain the primary evidence. No orders or new deployment. All failed and prior experiments retained.
