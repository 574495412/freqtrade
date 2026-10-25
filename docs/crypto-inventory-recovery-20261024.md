# 库存策略入场恢复与风险止盈：三个完整账户

最新后续：又完成[4个BTC残差／动量完整合约账户](crypto-btc-residual-20261024.md)，近期累计32个，尚无合格实盘版本。本文保留原实验结果。

2026-10-24。此前25个完整合约账户已按[官方来源多指标协议](crypto-performance-scorecard-20261024.md)重评。本轮完成另外3个2024全年原生合约账户，最近直接策略研发共28个账户，包含风险／成本对照及跨年验证。**三个新候选均失败，尚无满足用户联合目标的实盘版本。**

## 假设与固定对照

前一版大额单层虽然74.84%净胜率，平均盈利仅0.710USDT、平均亏损2.291USDT。新假设是：更迟的恢复入场能否减少继续下跌／上涨造成的损失，按实际止损风险设定收益能否改善盈亏结构。它们是待检验假设，不是观测亏损后对效果的保证。

[冻结协议v002](../research/crypto/inventory_recovery_risk_plan_v002.json)在任何经济账户开始前登记，三候选共享相同40币、1000USDT、2倍永续多空、0.05%／边原费率＋3bps／边额外成本及真实历史资金费：

- **InventoryRecovered**：原规则在EMA带外直接入场，改为此前3根完成5分钟K线曾超过原阈值，当前完成收盘回到阈值内、超过前收盘，实体方向恢复，收盘位置在上方60%以上／下方40%以下。保持原4小时EMA200方向及大额层优先。
- **InventoryRiskTarget**：原入场不变，止盈从固定保证金百分比改为实际已成交仓位止损现金风险的0.75倍（小额）／1.5倍（大额），包含两边已知费用和已累积负资金费，不把正资金费计作风险折扣。按原生带费用的开仓价值与杠杆换算ROI。
- **InventoryRecoveredRisk**：同时使用上述两项变化。

原有固定绝对止损、最多3次等额建仓、初始风险预算／保证金上限、小额4小时及大额72小时时限均保持冻结。[原InventoryGrid3](crypto-performance-scorecard-20261024.md)是既有对照：净亏4.97%、完整仓位胜率65.16%、Sharpe代理−1.550、小时mark回撤6.78%。没有把小额／大额单独账户相加代替同账户结果。

## 结果

| 候选 | 净收益 | 完整仓位 | 净胜率 | Sharpe代理 | 小时mark回撤 | PF | 小额净PNL USDT | 大额净PNL USDT |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| InventoryRecovered | -6.62% | 2391 | 62.90% | -2.040 | 7.29% | 0.779 | -37.67 | -28.56 |
| InventoryRiskTarget | -7.60% | 1988 | 52.06% | -2.014 | 8.39% | 0.765 | -45.12 | -30.90 |
| InventoryRecoveredRisk | -4.85% | 1990 | 50.15% | -1.299 | 6.25% | 0.845 | -39.24 | -9.30 |

Sharpe使用固定初始基数的币安式日收益本地代理，不是平台公布的实盘值。含浮亏回撤按小时mark观察，未覆盖盘中最坏路径；原经济门槛仍取原生闭仓回撤与小时mark回撤较大者。完整Sortino、收益回撤比、原生Calmar／SQN／期望、30／60／90日及月度结果均在每个账户的`official_metric_scorecard.json`中。

所有候选的小额交易频率超过原研究目标3笔完整仓位／自然日，但两层均净亏，PF均小于1且Sharpe代理为负。交易足够频繁没有转化为正期望。组合版本仅小幅减少原基准的净亏，胜率却降至50.15%，不能称为更优或合格策略。

恢复入场、风险止盈、两者组合的总费用分别约54.36／48.05／47.45USDT；去掉这些费用但保留资金费后的PNL仍约−11.86／−27.97／−1.08USDT。失败不能只归因于成本。提高止盈后的两个账户分别有1688／1724笔因原持仓时限退出，只有87／79笔ROI退出；较大的目标未在原交易时限内兑现。此描述不证明延长持亏会改善表现。

按冻结准入规则，三者不进入0／10bps成本档或2025年验证。既有80%完整仓位净胜率、50%年收益、3笔小额完整仓位／天等是项目研究假设，绝不是官方通用上线门槛。本次3个账户均为此前使用过的2024历史上的回顾式研究，不是独立样本外证据。

## 核验与复算

- 9项BTC／ETH／SOL真实历史前缀检查通过，验证原生高周期合并、指标与恢复入场信号在截断行情后保持一致。
- 6项新测试覆盖原生多／空、费用、2倍杠杆以及正／零／负资金费条件下，ROI目标与净现金收益一致；与原7项指标测试合计13项通过。
- 三个账户均逐笔复核现金与建仓风险、保证金、等额追加及唯一完整退出。最大现金误差约5.01e−9USDT；独立从原始小时净值重算PNL、Sharpe代理和最大回撤，误差分别不高于4.27e−14USDT、4.45e−16及5.56e−17。341个冻结原生Python源码文件未变。
- 代码：[策略](../research/crypto/strategies/inventory_recovery_risk.py)、[前缀检查](../research/crypto/check_inventory_recovery_prefix.py)、[实验入口](../research/crypto/run_inventory_recovery_study.py)。准备阶段v001仅因回执目录不存在失败，未启动任何经济账户；工程处置保留在旧目录，v002在运行前重新登记。未修改已完成实验。

在新输出目录复算：

```sh
.venv/bin/python -m research.crypto.check_inventory_recovery_prefix \
  --plan research/crypto/inventory_recovery_risk_plan_v002.json \
  --output user_data/crypto_quant/新的目录/causality_checks.json
.venv/bin/python -u -m research.crypto.run_inventory_recovery_study \
  --plan research/crypto/inventory_recovery_risk_plan_v002.json \
  --output user_data/crypto_quant/新的目录
```

结果位于`user_data/crypto_quant/inventory_recovery_risk_run_v002/summary.json`与`independent_review.json`，每个账户保存原生ZIP、完整净值、独立风险审计、日收益和全部指标。本轮没有真实订单、提交、推送或生产部署。
