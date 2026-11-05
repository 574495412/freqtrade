# 现货五分钟账本重建：独立时序审阅

2026-11-05。本次是对已完成的 **X8 现货、13bps 单边费用**长周期账户及重建代码
的独立审阅，不是另一套交易回放，也不是实盘成交审计。范围为 2025-01-01 00:00 至
2026-08-31 23:00 UTC；36 个交易对，初始模拟钱包 1,000 USDT。只核查这一账户，
其他成本或策略不能自动继承本结论。

## 与固定 Freqtrade 引擎时序的吻合

固定版官方引擎在每根主周期 K 线的订单处理前调用 `bot_loop_start`；原有观察器在
该回调读取净值。五分钟重建使用 `searchsorted(..., side="right")`，使时间戳等于
当前 K 线的订单在当前观察之后生效，下一次五分钟观察才计入；这与该原生回测观察
点顺序一致，不代表交易所中的真实成交时间。

从该账户原生结果归档读取到 183 个完整持仓、457 条订单，457 条的成交时间戳均在
五分钟网格上。13bps 入场和出场成本、持仓数量及剩余数量平仓手续费准备按原回测
模型重建。14,592 个共同整点与既有小时净值的最大绝对差为 **4.01e-8 USDT**；在
共同终点 2026-08-31 23:00 UTC 不存在当刻或之后的订单，五分钟重建现金和参考净值
均约为 **2,238.894623 USDT**，仓位为零（余留约 3e-14 USDT 为浮点计算误差）。
全时段五分钟 OPEN 参考最大回撤为 **9.2523%**，只代表这一网格上的观察，不覆盖
五分钟 K 线内部的价格路径或真实盘口成交。

## 成交数量和费用的适用边界

该原生回测的 `Order.close_bt_order` 把 `filled` 设为 `amount`，因此在这个固定的
全额成交回测模型内，使用导出的 `amount` 重建 457 条成交数量有源码依据。但
Freqtrade **minified 回测导出不包含 `filled`、`status` 和逐次部分成交明细**，且
脚本对所有导出订单按完整数量处理。它无法识别真实交易的部分成交、撤单后残余、
分笔成交时间或币本位手续费，不能用于声明实盘账本已审计。单边费用是回测假设，
不能证明实际账户费率。`minimum_cash_usdt` 是总现金参考值，未扣未成交买单冻结，
不能当作真实可用余额。

## 计划哈希检查缺口

本次被审阅的原版脚本哈希为
`4e61ad58fd1bbdffd0d8a0affb43c84e6085278a3624787eacd3908e6fd31500`。该版本
校验原生结果、结果摘要与数据文件哈希，但没有在读取时核对当前注册方案哈希与
既有独立账户审计内的 `plan_sha256`，也没有校对账户子目录内的方案副本。读取既有小时
净值时，该版本同样没有对照原审计中的 `curve_sha256` 验证文件哈希，而是直接比较
净值数字。方案或曲线被替换时，可能把不同输入与旧审计结果混用。

本次人工核对时，根目录注册方案、账户子目录方案与既有账户审计的方案哈希**均为**
`ee471524b1b3892a9a85219b3d3ac04e8a7837a69157000ebe489f59d1763d60`，所以当前样本
未发现该哈希不一致。另已人工计算既有小时曲线哈希为
`fb9baccc699c9f0d3cd869bf63e37ecb88e5b2d6dc90e285a8879b903a06b0e4`，与独立账户审计
记录相同。

新建的 [v3 重建脚本](/Users/chrise/GitHub/freqtrade/research/crypto/audit_spot_5m_order_equity_20261105_v3.py)
已加入根方案哈希、账户方案内容及小时曲线哈希三项校验，脚本 SHA-256 为
`1ba1c39694ee273406d756345af45650872cc4a59d552650f55f55436066438a`。审阅时在临时副本
中分别篡改根方案、账户方案和小时曲线，三种情况均在计算前被拒绝；v3 生成的
175,093 行五分钟数据与原版逐值相同，整点最大差仍为 4.01e-8 USDT。原版和 v3 的
脚本、结果均单独保留，历史原件未被覆盖。

## 原件与核验范围

- [被审阅的五分钟重建代码](/Users/chrise/GitHub/freqtrade/research/crypto/audit_spot_5m_order_equity_20261105.py)。
- [五分钟结果及哈希](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/independent-5m-order-equity.json)。
- [v3 五分钟结果及哈希](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/independent-5m-order-equity-v3.json)。
- [独立账户现金审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/spot_baseline_long_run_20261105_v001/nfi-x8-spot-cost13/independent-account-audit.json)。
- 固定引擎的 [K 线循环时序](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/optimize/backtesting.py:1662)
  和[回测订单全额成交实现](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/persistence/trade_model.py:304)。

这些检查独立于五分钟重建实现，但使用相同原生模拟订单及行情；它们不是第三方
审查、独立样本外证明、交易所订单回执，也不保证实际滑点、容量或最大损失。
