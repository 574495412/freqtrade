# NFI固定版本复现记录

2026-10-23。已完成固定X7源码、六币数据、24个原生回测账户、训练期TradingAgents审查及未来数据检查。**分段样本不足且存在作者历史调参污染，尚未晋级或上线。**

## 范围与版本

- NFI X7 v17.5.175，commit `1af55971d3a64703ec449078e09fdec257d0d3c6`；原版3倍与仅杠杆改为1倍的对照。
- BTC、ETH、BNB、SOL、XRP、DOGE；本金1000USDT、六仓、unlimited资金配置，保留原生加减仓和退出。六币是本项目受限组合，非作者建议的40–80币组合。
- 5m信号及15m/1h/4h/1d辅助周期。原始5m从2023-08起，日线从2021-10起，满足2024年开始前至少800日线暖机。576份官方月档经SHA校验，5m共1947456根，真实资金费和mark来源复用首轮封存收据。
- 每侧5bps基础手续费与真实资金费，另加0/3/10bps费用代理；非真实盘口成交保证。

## 经济结果

以下均为整段净收益、非年化。回撤采用原生已平仓回撤与小时浮亏观察回撤中较大者。小时估值在当前K线撮合和资金费处理前，使用真实mark开盘，保留预计平仓费；不表示盘中最差回撤。

| 区间 | 版本 | +0bps收益 | +3bps收益 | +10bps收益 | +3bps回撤 | +3bps胜率 | +3bps完整交易 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024训练 | NFIReference3x | 39.33% | 38.39% | 36.82% | 3.60% | 100.00% | 31 |
| 2024训练 | NFILeverage1x | 11.82% | 11.56% | 11.11% | 1.20% | 100.00% | 31 |
| 2025分隔 | NFIReference3x | 22.71% | 22.04% | 22.15% | 4.01% | 100.00% | 28 |
| 2025分隔 | NFILeverage1x | 6.87% | 6.68% | 6.70% | 1.36% | 96.43% | 28 |
| 2026年1–8月分隔 | NFIReference3x | 1.57% | 1.53% | 1.45% | 1.31% | 100.00% | 3 |
| 2026年1–8月分隔 | NFILeverage1x | 0.52% | 0.51% | 0.48% | 0.44% | 100.00% | 3 |
| 2025–2026年8月连续 | NFIReference3x | 15.16% | 14.77% | 14.91% | 9.27% | 96.77% | 31 |
| 2025–2026年8月连续 | NFILeverage1x | 8.39% | 8.18% | 8.19% | 1.36% | 100.00% | 31 |

两版在2025年均只有28笔、2026年前8月均只有3笔，低于事前30笔/分段目标。连续账户通过经济筛查不能替代两段分别验收。部分更高费用档收益略增，是路径与原生退出随净收益改变所致，不能称费用带来alpha。

原生在没有亏损交易时将PF显示为0。保留原始字段，另在economic_audit.json报告毛利、毛损和NO_LOSSES_OBSERVED；用毛利≥1.1×毛损校验同一比值门槛，避免零除与误读。未出现亏损不是尾部安全证据。

## 因果与研究身份

TradingAgents原生多空研究员实际调用3次，与固定训练数值规则都选择原版3倍；选择落盘后才开始2025及之后计算。未证明模型比固定规则增加经济收益。原生lookahead-analysis对两个版本各检查20个信号，均未发现其覆盖的未来数据泄漏。检查保留limit订单，原生分析器会改变资金和仓位数，故该检查仅用于泄漏诊断。

**未来数据检查通过不等于策略没有历史调参污染。**当前X7源码在第31405–31409行明确标注2026-04-18损失案例和依据2026+2022筛阈值，第31496–31499行又提到2026年具体事件。故2026年及此前结果不能当作者未见样本外；原协议中“作者优化历史未知”的限制现已有更强的源码证据。没有删除这些条件或按本轮结果再调参。

原生订单现金流、手续费、资金费与完整交易PnL的最大对账误差小于0.00000001USDT。小额容量诊断按同一小时全部成交名义额/之前完整小时成交额计算，最高约0.00312%；当前未出现需要复杂盘口容量研究的证据。

## 可复查入口

- 协议：`research/crypto/nfi_plan.json`。
- 数据准备：`research/crypto/prepare_nfi.py`；原生回测与只读浮亏观察：`research/crypto/run_nfi.py`。
- 训练审查：`research/crypto/review_nfi.py`；经济比值审计：`research/crypto/audit_nfi.py`。
- 全部结果：`user_data/crypto_quant/nfi_run_v001/`；原生每账户ZIP、result.json、hourly_equity.parquet、freeze.json均保存。
- 总评evaluation.json；选型selection.json；模型原文agent_response.json；泄漏汇总bias_result.json；现金流与容量economic_diagnostics.json；代码身份study_identity.json。

```sh
.venv/bin/python research/crypto/run_nfi.py \
  --dataset user_data/crypto_quant/nfi_data_v001 \
  --source user_data/crypto_quant/external_sources_20261022/iterativv_NostalgiaForInfinity/NostalgiaForInfinityX7.py \
  --output user_data/crypto_quant/新的账户目录 \
  --variant NFIReference3x --phase train --cost 3

.venv/bin/python -m research.crypto.review_nfi \
  --run user_data/crypto_quant/训练根目录 \
  --tradingagents-python ../CZ-Agent/.venv-tradingagents-lab/bin/python \
  --tradingagents-root ../TradingAgents
```

每个版本、阶段、成本独立目录；新目录不覆盖旧结果。review入口要求两个版本三档训练结果齐备，并核对源码及时间边界。旧NFI4的默认杠杆与新X7不同，不能误标为同一个原版3倍。

## 下一步

已按“默认分支截至2023-12-31的最后一次提交”规则取得commit `16395b32d3c36ba728b2b489e9e51e44d3a8ff8c`。其Docker配置默认策略为NFI X4 v14.0.724，源码SHA `e2885c502ae2d069e2aa69d00c29fd10c3fec7f5eb7a2ba9b471d82606dea6eb`。保留这一版本作评价期之前的对照；不依据它在2024之后的表现挑选版本。另继续资金费因子的固定增量实验。当前未新增NFI真实交易、推送或部署。
