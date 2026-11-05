# 40 币历史账户与发布后短期检验

2026-11-05。此前六币对照范围过窄：在固定的 40 币、1 倍、8bps 和共同账户合同下，
NFI X8 的历史净收益明显高于六币版本，也高于本轮对照的旧 X4 和 X7；X7 的小时观察
回撤更低。这里仍只比较三款策略，不能据此宣布跨全部策略、币池或未来行情的最优者。

## 相同 40 币、1 倍和 8bps 的连续账户

币安 USDT 隔离永续合约，各账户初始资金 1,000 USDT、最多 6 个持仓，2025-01-01 至
2026-08-31 23:00 UTC 共同终点；单边成本为 5bps 手续费加 3bps 费用代理，另计模型
资金费。三款各保留原有方向、加减仓、退出和预算规则，1 倍不代表相同的实际风险。
这里不是现货：X8 和 X7 可做多、做空，X4 compact 是只做多对照。此前单独提及的
GeneTrader +231.74% 是现货报告，不能与这些合约账户直接混排。
8bps 长历史账户实际完成的多／空持仓分别为：X8 187／99，X7 185／22，X4 68／0。

| 策略 | 连续期净收益 | 小时持仓 mark 观察回撤 | 完整持仓 | 2025 切片 | 2026 年至 8 月切片 |
| --- | ---: | ---: | ---: | ---: | ---: |
| NFI X8 v18.0.146 · 40 币 | +141.21% | 3.82% | 286 | +102.83% | +18.92% |
| NFI X7 · 40 币 | +95.45% | 2.77% | 207 | +74.44% | +12.05% |
| 旧 NFI X4 compact · 40 币 | +24.00% | 11.84% | 68 | +21.43% | +2.12% |

两个日历切片来自同一连续账户，带入资金和未平仓；不能将其视为独立试验。小时
回撤是以 mark OPEN 观察全持仓的结果，含未平盈亏和剩余退出费准备，并非盘中最坏
回撤或真实可成交价格。X4 结果与已有 40 币账户的 +24.00% 一致。

此前同引擎、同成本、1 倍、相同连续区间的 **六币 X8 为 +10.76% / 1.16% 小时观察
回撤**。币池显著改变交易机会和实际风险，不能把六币结果用作 X8 宽币池的收益上限。
两次配置中的共同币种顺序也不同，原生同时信号的处理顺序可能受影响；因此差额不能被
解释为“只增加币种数量”的独立因果效果。
原作者 2025 年 1 月的 +81.89% 是 3 倍、10,000 USDT、223 个实际加载标的的另一份
账户，不能直接和本表加总或等同。

## 作者提交后的短期历史账户

X8 源码提交时间为 2026-10-22 12:10 UTC。在获知上表 X8 8bps 结果后、读取新时间段
经济结果前，登记了 2026-10-23 00:00 至 2026-11-04 23:00 UTC 的固定测试。账户
重新以 1,000 USDT 空仓开始，使用相同 40 币、1 倍和 8bps；结果为 **+0.92%**、
小时观察回撤 **0.89%**，完成 **5 笔**持仓。

这段行情发生在源码提交之后，但测试是在其结束之后才登记和执行的，属于短期回看的
时间外检验，不是实时前向或真实账户收益。空仓重启与从更早日期持仓延续也不同；只有
5 笔持仓，不能证明长期盈利或判断极端行情风险。

随后登记的同口径 X7 对照在相同短期区间取得 **+0.94%**、小时观察回撤 **0.28%**，
完成 5 笔持仓；其 5 笔持仓和 10 笔成交同样通过现金复算。X7 方案是在看到 X8 结果后
登记的。两账户中有 4 笔持仓的交易对和开仓时间相同，不能把这 10 笔合并当作独立样本。
这段短期样本中 X7 略好，差异不足以确立长期优势或自动切换模拟账户。

新数据使用历史预热文件及币安公开 2026 年 9 月至 11 月档案。完整检查发现 10 月
月档缺少价格和 mark 的部分日期，对应日档返回 404；最终用币安公开 K 线接口原始响应
补齐。归档核对发布方校验值，接口响应另存并记录哈希；两者均检查连续时间网格，失败
记录也予以保留。资金费来自公开归档与接口实际
事件；40 币、9 月至 11 月的 8,580 条原生资金费记录与来源逐条核对，9—10 月的
发布结算间隔未发现异常。未插入合成的结算事件；这不证明客户账户的真实资金费结算。

## 审计、成本压力与限制

- 三个 8bps 长历史账户共 561 笔完整持仓、1,469 笔成交，通过既有独立 Decimal 成交
  现金复算。逐笔最大误差约 `2.92e-8 USDT`；共同小时净值、费用、杠杆和源码副本
  也经过复核。该复算是独立实现，不冒充第三方审查。
- X4 新旧 68 笔持仓、202 笔成交的指定字段逐项吻合；费率存在约 `1.08e-19` 的
  浮点表示差异。原始严格比较和带明确容差的比较均保留，未核对的字段不声称一致。
- 发布后短期账户 5 笔持仓、10 笔成交也通过现金复算。已保留原生结果、净值、费用、
  资金费、源码、运行日志和输入哈希。
- 15bps 的成本压力测试另行登记，登记发生在看到 X8 8bps 结果之后。X8 在
  相同 40 币与共同终点下为 **+135.66% / 小时观察回撤 4.52%**，终点前完成 281 笔
  持仓，另有 1 笔未平仓并已计入净值；X4 为 **+23.35% / 11.86%**、68 笔完整持仓；
  后续登记的 X7 为 **+84.13% / 5.02%**、198 笔完整持仓，已独立复算。
  三个账户均通过现金复算。成本代理不证明盘口可成交或实际滑点上限。
- 40 币池是既有历史池与当时可交易池交集，存在幸存者偏差；公开交易规则快照也不是
  已认证的逐日历史规则。X4 使用既有 float32 指标压缩包装；X8 没有新增降精度。
- 使用官方 Freqtrade `recursive-analysis` 对 BTC 单标的和一个固定时点做了指标诊断。
  在策略默认 800 根预热下，`OBV_change_pct` 为 −0.5129，长历史基准为 −2.7555；
  15 分钟版本分别为 0.1341 和 0.0590。两个指标在源码中参与信号条件。该时点所核对
  的 `>0`、`<0` 和 `<50` 判断相同，但不能外推到其他币和时点。工具的指标级单点
  未来函数检查未报偏差；它不是完整策略级未来函数检查。见[官方递归检查说明](https://www.freqtrade.io/en/stable/recursive-analysis/)
  和[原始数值](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_recursive_check_20261105_v003/exact-values.json)。
- 直接调用递归工具曾在固定源码目录生成 `.pyc`，被完整性检查正确拒绝。已仅清理本轮
  生成的缓存并记录哈希；诊断脚本现禁用字节码生成，复跑得到相同数值且没有重生缓存，
  策略源码保持原始哈希。源码及运行入口测试重新通过。
- X8 完整未来函数与信号级预热稳定性检查、盘口成交与容量、交易所保护单、真实资金费
  和账户状态仍未取得。发布前长期历史属于已知数据，不能称作独立样本外验证。
- 当前原版 X8 使用 `stoploss=-0.99` 且 `stoploss_on_exchange=false`；历史观察回撤
  不是账户最大亏损保证，也没有证明交易所端保护单已建立。

已启动独立的 40 币本地 dry-run，使用新 profile、独立账户绑定与数据库；原六币模拟
进程及数据库保留。启动日志显示 Freqtrade RUNNING 且所有订单均模拟，启动不证明自然
成交、盈利或非空持仓重启收敛。详情见 [40 币模拟配置及状态命令](/Users/chrise/GitHub/CZ-Agent/ops/freqtrade/releases/nfi-x8-forty-1x-20261105/README.md)。
[状态回执](paper-status-receipt.json)记录了检查时两个 PID、独立数据库、心跳和模拟订单提示；
当时两边均为零笔交易。运行入口与源码检查相关的 64 项离线测试通过。

本轮没有开启真实订单或部署远程服务器。现有账户只读认证仍须修复并核验，实盘资金
上限、最大可接受回撤及目标服务器也尚未确定。
此前六币方案的验收哈希是当时的历史快照；新增 profile 和当前入口文档的变化不覆盖旧
回执，也不应拿旧回执声称当前工作树逐文件未变。

## 原件

- [40 币冻结方案](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/README.md)
  与 [原生账户比较及审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_comparison_20261105_analysis_v001/comparison.json)。
- X4 新旧订单的[严格字段比较](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/x4-prior-parity.json)
  与[费率容差比较](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/x4-prior-parity-tolerant.json)。
- [发布后短期协议](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/post-source-period-protocol.json)、
  [结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_account_20261105_v001/nfi-x8-1x-cost8/result.json)与
  [独立现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_account_20261105_v001/nfi-x8-1x-cost8/independent-account-audit.json)。
- [X7 短期方案](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/plan-post-source-x7.json)、
  [结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_x7_account_20261105_v001/nfi-x7-1x-cost8/result.json)及
  [现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_x7_account_20261105_v001/nfi-x7-1x-cost8/independent-account-audit.json)。
- [原始数据下载清单](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001/sources.json)、
  [官方接口补充清单](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_inputs_20261105_v001/supplement_api_oct/sources.json)、
  [数据覆盖](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi40_post_source_data_20261105_v002/coverage.json)。
- [15bps 压力方案](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/plan-cost15.json)
  、[X8 结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_cost15_20261105_v001/nfi-x8-1x-cost15/result.json)
  、[X8 现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_cost15_20261105_v001/nfi-x8-1x-cost15/independent-account-audit.json)
  与 [两账户比较审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_cost15_20261105_analysis_v001/comparison.json)。
- [X7 的 40 币补充计划](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/plan-x7.json)
  登记时 X8 和 X4 的部分 40 币结果已知；[8bps 结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_x7_20261105_v001/nfi-x7-1x-cost8/result.json)、
  [现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_x7_20261105_v001/nfi-x7-1x-cost8/independent-account-audit.json)和
  [15bps 结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_x7_20261105_v001/nfi-x7-1x-cost15/result.json)及
  [现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/wide_strategy_x7_20261105_v001/nfi-x7-1x-cost15/independent-account-audit.json)。
