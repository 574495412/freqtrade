# 现货历史账户：成交假设与五分钟风险参考

2026-11-05。这里是对已完成的币安现货 Freqtrade 历史账户的描述性核验，时间均为
UTC。X8 v18.0.146 的固定源码在研究窗口之后提交，长期结果属于事后历史回测。
原始 Freqtrade 成交及账户资料均为模拟，价差、排队、部分成交、盘口深度、交易所故障
和延迟尚未认证，不据此声称能在真实账户取得相同收益。

## 独立五分钟账本

独立脚本从原生每笔已成交订单重建现货现金和持仓，在每个 5 分钟开盘前快照：总现金加
持仓数量乘现货 OPEN，并按账户费用率预留卖出费。该时刻成交的订单从下一快照起计入。
逐小时结果和原观察器独立复算，最大差异约 `4.01e-8 USDT`。这些数据不含尚未成交的
挂单占用；总现金不等于可下单余额。5 分钟 OPEN 也不能代表 K 线内部最差价格。

X8 的 1,000 USDT 现货模拟账户在 2025-01-01 至 2026-08-31 23:00，单边 13bps
总成本假设下，净收益 +123.89%，小时 OPEN 观察最大回撤 8.00%，五分钟 OPEN 观察
最大回撤 9.25%；五分钟快照中已持现货总额 / 净值最高 99.26%。20bps 压力账户相应为
+107.18%、8.04%、9.30%、99.40%。此处每个成本档为独立账户，不是共享资金。

X7 的同区间 13bps 账户为 +81.10%、小时回撤 3.58%、五分钟参考回撤 6.78%、五分钟
峰值持有现货总额 / 净值 99.22%，169 笔已完成，终点另有 1 笔未平并计入净值。同档
X8 历史收益更高，X7 的五分钟观察回撤更低，选择取决于资金和风险约束；都不是已验证
的实盘结果。X7 在 20bps 下为 +77.44%、小时回撤 4.79%、五分钟参考回撤 8.53%、
五分钟峰值持有现货总额 / 净值 99.38%，165 笔已完成，终点有 1 笔未平并计入净值。
四账户完整对照见[审计后现货比较](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_analysis_20261105_v001/comparison.json)。

X7 终点未平的 CRV/USDT 从 2025-07-29 开仓，持续约 398 天；原生归档在回测结束后
强制平仓，不是自然退出。该持仓归档含 167 笔成交（包括末尾强制平仓），累计买入名义
额约 4,312 USDT，反映反复加减仓及资金循环，并非同一时刻持仓金额。169 笔已完成
持仓虽无亏损，也不能称账户没有亏损或胜率得到实盘验证；较长的持仓时长、费用和未平
风险须结合净值观察。

五分钟账本 v3 对根方案、账户方案、原结果、原生归档、小时曲线与行情哈希进行核验；
独立负向测试在临时副本篡改根方案、账户方案、小时曲线时均在写出结果之前拒绝。
前两版和其产物保留，见[审计脚本](/Users/chrise/GitHub/freqtrade/research/crypto/audit_spot_5m_order_equity_20261105_v3.py)、
[负向测试](/Users/chrise/GitHub/freqtrade/research/crypto/test_spot_5m_v3_guards_20261105.py)、
[13bps 结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/independent-5m-order-equity-v3.json)和
[20bps 结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost20/independent-5m-order-equity-v3.json)。

冻结的 `spot_observer.py` 中存在未调用的 `finish()`，其旧收据文字提到期货 mark 和资金费；
当前 runner 未调用该函数，也没有生成那份收据。实际结果来自币安现货的 5 分钟及小时
OPEN，且已检查无空头、无资金费。为了保持冻结源码完整性，未修改旧文案。

## 2025-10-10 极端行情

13bps 账户中，在 2025-10-10 20:00 至次日 00:00 之间开仓并平仓的 13 笔持仓合计
账面利润 357.36 USDT，为全期净值增量 1,238.89 USDT 的 28.85%。其中 AVAX/USDT
2025-10-10 21:20 按 11.45 买入，21:25 按 17.80 卖出，账面利润 136.59 USDT。
这段时间是发现极端行情后选择的事后归因，不能把占比理解为删去交易后的策略收益：
账户现金、后续仓位与信号都可能变化。见[原生订单归因](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/extreme-event-realized-attribution-v2.json)。

X7 的 13bps 账户在相同时段有 12 笔持仓开平、账面利润共 160.78 USDT，占其全期
净值增量约 19.83%；同样属于事后归因。按后述固定数量、下一根 OPEN 机械改价时，
这些交易合计变为 34.56 USDT，不是重跑账户的收益。见[X7 归因](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x7-spot-cost13/extreme-event-realized-attribution-v2.json)
及[敏感性演算](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x7-spot-cost13/extreme-event-5m-delay-fixed-quantity-sensitivity-v2.json)。
X7 的 20bps 对照对应 156.35 USDT、占全期净值增量 20.19%；同一机械改价得到
31.50 USDT，解释范围相同。

币安现货 K 线及公开逐笔样本均记录了上述两个价位。21:20 这根 AVAX 五分钟 K 线低点
8.52、高点 27.80；21:25 的第一份逐笔接口样本达到 1,000 条上限，仅覆盖该秒的前一
部分。随后按聚合成交 ID 分页，该秒共观察到 1,355 条，ID 连续且时间顺序一致。因此
价格确有公开成交，但不能证明策略订单当时可排到这些价格。见[原始样本收据](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_inputs_20261105_v001/long-raw/avax-aggtrades-sample-receipt.json)
和[后续分页收据](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_inputs_20261105_v001/long-raw/avax-aggtrades-212500-complete-second-receipt.json)。

作为价格敏感性演算，保持原 13 笔持仓的订单数量和费率，把每笔原成交价格机械替换为
下一根五分钟现货 OPEN，合计利润从 357.36 变成 121.05 USDT。这不是延迟回测，亦不
代表下一根开盘价必然可成交；没有重新计算资金约束、挂单、生效时序或后续策略决策。
见[敏感性原件](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/extreme-event-5m-delay-fixed-quantity-sensitivity-v2.json)。

其余已完成订单中，DYDX/USDT 有一笔模拟卖单数量达到整根五分钟 K 线成交量的 19.73%。
整根 K 线成交量包括模拟成交时点之后的成交，不是该时刻可用深度，不能作为容量证明。
见[逐笔 K 线核验](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/fill-candle-evidence-v2.json)。

以 13bps 账户的原生归档为范围，X8 的 457 笔和 X7 的 529 笔模拟成交价均等于相应
五分钟 K 线的 OPEN（比较容差为相对和绝对 `1e-10`，包含原生回测终点强制平仓）。
K 线首笔成交价不保证策略订单可以同价成交；其中 X8 有 43 笔、X7 有 46 笔模拟订单
的数量超过整根五分钟 K 线成交量的 1%。见[X8 明细汇总](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/open-fill-statistics.json)和
[X7 明细汇总](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x7-spot-cost13/open-fill-statistics.json)。
20bps 下 X8 的 503 笔、X7 的 523 笔归档成交也全部等于对应五分钟 OPEN。
