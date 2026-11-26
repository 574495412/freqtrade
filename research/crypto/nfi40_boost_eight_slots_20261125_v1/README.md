# NFI X8 首仓放大与 8 仓上限

`NFIBoostEightSlots` 沿用 `FlatTopBoost`，只为符合原有条件的 Top Coins 多单把首仓乘以 1.25。并发上限从 6 提高到 8，钱包首仓提案仍按 6 仓公式和当时的可用资金计算。原 NFI 入场规则、依赖空余仓位的模式门槛、出场、加减仓和交易所约束都由同一个原生引擎执行。源码冻结为 NFI X8 v18.0.146 和 Freqtrade `c051e9065c5b677f03817dd1f83cb980af1ed684`。 [策略](NFIBoostEightSlots.py) · [筛选规则](protocol.json) · [冻结方案](plan.json)

## 比较口径

主窗口为 2025-01-01 00:00 至 2026-08-31 23:00 UTC。三个账户均使用同一有序 40 个 Binance USDT 永续合约、1,000 USDT 空仓起跑、1 倍逐仓和 5 分钟撮合。单边 15bp 为主择成本，8bp 用于成本敏感性比较。账本计入模型资金费。以共同整点的标记价估值，并对当时的未平仓敞口预留退出费用。开仓数按不同持仓的首笔实际成交计数，加仓单另计。收益和回撤来自完整账户权益。 [原账户合同](../mainline_comparison_contract_20261117_v1/README.md)

独立复验预留 2026-11-05 00:00 至 11-24 23:00 UTC，两个对照与候选各自空仓起跑，采用单边 15bp。主窗口胜出后才运行该窗口的账户撮合。20 天只覆盖 14 笔候选开仓，证据集中在少量机会。小时回撤有观察粒度限制。原主合同还保留旧撮合精度、历史合约资格和市场规则的局限。研究范围是离线回放。 [独立复验方案](holdout_plan.json) · [复验对账](holdout_comparison.json)

## 相关试验

| 主窗口单边 15bp | 累计净收益 | 小时最大回撤 | 独立开仓 |
|---|---:|---:|---:|
| NFI X8 原版 | +135.66% | 4.52% | 282 |
| 原版只增加到 8 仓 | +141.37% | 4.65% | 292 |
| 6 仓固定放大首仓 | +161.84% | 4.26% | 279 |
| 6 仓固定放大首仓并延后止盈 | +161.53% | 5.28% | 279 |
| **8 仓固定放大首仓** | **+165.17%** | **4.26%** | **287** |

原版单独增仓触发登记的回撤否决条件。延后止盈处理了 32 笔，组合净收益低于固定首仓放大版。此前按相对 BTC 强度分配首仓的试验也未超过固定放大。8 仓组合在主窗口新出现的 9 笔成交共净赚 26.13 USDT，固定放大版独有的一笔赚 5.81 USDT。两个完整账户的资金路径同时变化。 [单独 8 仓](../nfi40_eight_slots_20261125_v1/comparison_15bp.json) · [延后止盈](../nfi40_boost_profit_tail_20261125_v1/comparison_15bp.json) · [相对强度分配](../nfi_strength_allocation_20261107/README.md) · [容量归因](capacity_attribution_15bp.json)

## 成本与独立窗口复验

| 比较 | 原版累计净收益 | 6 仓固定放大 | 8 仓固定放大 | 6 仓 → 8 仓开仓数 | 6 仓 → 8 仓最大小时回撤 |
|---|---:|---:|---:|---:|---:|
| 主窗口 15bp | +135.66% | +161.84% | +165.17% | 279 → 287 | 4.26% → 4.26% |
| 主窗口 8bp | +141.21% | +169.68% | +172.95% | 283 → 290 | 4.18% → 4.18% |
| 独立窗口 15bp | +2.38% | +2.72% | +2.95% | 12 → 14 | 1.61% → 1.61% |

主窗口 15bp 相对 6 仓放大版增收 33.34 USDT，其中 2025 年账户差额扩大 26.20 USDT，2026 年 1–8 月扩大 7.14 USDT。8bp 分别扩大 25.49 和 7.20 USDT。15bp 最佳单笔盈利从 80.03 增至 80.68 USDT，前五笔盈利占已平仓毛盈利的比例从 14.93% 变为 14.76%。增量主要来自更多独立开仓和随后的钱包路径变化。独立窗口新增的 GALA 和 SOL 空单合计净赚 2.32 USDT。 [15bp 对账](comparison_15bp.json) · [8bp 对账](comparison_8bp.json) · [九个账户的交叉核验](verification_summary.json)

线上合约部署归档采用日更 80 币、3 倍逐仓、12 仓和动态可用余额。本研究的收益和仓位结论适用于上述 40 币、1 倍历史账户。 [部署记录](../../../deploy/aliyun_20261111/README.md) · [日更 80 币历史比较](../nfi80_historical_universe_20261109/v003/REPORT.md)

## 复核与复跑

在 Freqtrade 项目根目录，使用已锁定依赖的 `.venv-paper` 执行复核。账户原始 ZIP、逐笔现金审计及小时净值位于 `user_data/crypto_quant/nfi40_boost_eight_slots_20261125_v1/`。

```sh
.venv-paper/bin/python -B -m research.crypto.nfi40_boost_eight_slots_20261125_v1.verify_study
```

如需重新计算一个主窗口账户，为原生入口指定新输出目录。下面以 15bp 为例。另一个成本档使用 `--cost-bps 8`。独立窗口使用 `holdout_plan.json`，分别选 `original`、`fixed_boost` 和 `candidate`。每次运行使用不同的输出目录。

```sh
replay_dir="$(mktemp -d)"
.venv-paper/bin/python -B -m research.crypto.strategy_comparison_wide_20261105.run_wide_comparison \
  --plan research/crypto/nfi40_boost_eight_slots_20261125_v1/plan.json \
  --candidate NFIBoostEightSlots --cost-bps 15 --output "$replay_dir/cost_15bp"
```
