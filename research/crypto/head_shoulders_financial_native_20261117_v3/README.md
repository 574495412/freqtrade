# 头肩40币完整账户：金融执行准备

**准备阶段已完成，4个头肩金融账户尚未启动。** 已冻结1000/500 USDT × 每边15/8 bp四个独立原生账户、源策略字节、完整窗口和淘汰门槛。当前`execution_release.json`不存在；控制器必须等待root复核及两户实际500 USDT NFI账户完成，再获得同一个金融worker文件锁。没有启动`Backtesting.start()`，没有新增头肩成交或收益结论。

## 固定合同

- 原40有序USDT永续币池、逐仓1x、每钱包6槽位、0.99可用余额、原数据。头肩源信号1h、原生撮合细节5m、市价单；窗口2025-01-01 00UTC至2026-08-31 23UTC共同小时开盘前估值，原生输入从2024-12-31 23UTC开始。
- `sources/HeadShouldersStrategy.py`与`HeadShouldersInputs.py`是封存v2的精确字节副本。原v1函数、order6、early=False、严格close退出、width−1到期、自终止mask、事件ID与重叠/重入处理均不变。
- 使用固定官方vendor commit `c051e9065c5b677f03817dd1f83cb980af1ed684`的撮合、钱包、默认inclusive资金费。没有接入Hawkes的两币固定tick或funding ownership扩展。
- 3 GiB进程RSS、1.5 GiB最低磁盘、4小时、单金融worker；原生窄1h DataFrame沿用已存在的原样dict存储适配，不新增数值降精度或金融序列化器。

`economic_protocol.json`在新头肩金融结果出现前冻结：每个资本的两费用都必须在三个声明窗口收益严格为正、小时标记回撤≤15%、完整24小时收益Sharpe>0.5；缺失或非有限指标拒绝，实际成交/现金/完整NAV/资金费独立审核必须通过。所有失败保留，不按结果调整规则。

组合只允许**实际HS500 + 实际NFI X8 500**，合计1000 USDT，逐小时净值金额相加，不从1000钱包线性缩放，也不将两个1000钱包合称1000。固定两独立钱包各6槽位，合计可能12槽位；同币相反方向不得抵消毛暴露或未知资金费。两费用下完整收益必须高于相同费用的原NFI1000，回撤不得更高，各声明年度窗口仍须为正。NFI1000基线是原40币旧条件模型记录，不是BTC/ETH两币结果或新验证利润。

## 先解决了真实精度差异

用实际vendor `history.load_data`与`get_tick_size_over_time`分别加载原NFI 5m/800根预热和HS 1h/零原生预热，在财务开始前冻结并完成40币月表及共同小时asof对比。

结果不是完全相同：**6币、10个月表行、6,648个共同小时tick不同**。默认路线的不一致结果、代码、日志及未执行的原runner草稿全部保留，没有以文案限定替代处理。

新登记的`LegacyFiveMinutePrecisionMixin`通过支持的`Backtesting.get_pair_precision`接口，返回原NFI5m确切月表及其原生`.asof`语义。它不改头肩1h信号。真实构造器上的**583,680次共同小时接口调用**全部与官方原NFI月表查询一致。该表仍使用整月后续OHLC观察，**没有修复前视，也不证明真实历史交易所tick/数量规则或币池资格**。

## 输入与原生准备验证

全部40币按顺序逐币只读两套原始行情：1,081,920根1h和12,983,040根5m，完整起点2023-08-01。每小时恰12根5m，UTC日历连续；聚合open/high/low/close与原1h **逐项严格相等，价格差异0**。volume按预冻`rtol=1e-10, atol=1e-8`比较通过，最大绝对浮点差为`2.384185791015625e-7`。未替换1h输入或放宽价格容差。

金融准备探针只运行一次成功的有限窗口重试：真实构造器、exchange.validate_config、DataProvider、全部40币各25根1h/300根5m、复制策略的指标与信号接口，以及完整共同小时精度查询全部通过。4份资金/费用配置的静态合同核对通过。回调错误强制中止和未释放队列拒绝用例通过，峰值RSS412,712,960 bytes。这个有限探针不证明四个完整金融进程的资源可行性；实际运行仍受资源门槛约束。

首次有限探针误把官方loader的stop时刻当成排除端点，读到26/301根后日历断言停止，实际exit1、0金融账户。原代码、日志、配置和`preflight_attempt1_failure.json`保留；`preflight_attempt2.py`仅把有限探针stop减1秒并独立冻结，未改变金融计划、窗口、策略或门槛。成功重试实际观察到exit0。原v2的38项完整回调测试没有重复替代为新收益证明。

## 财务审核及队列门槛

`native_runner.py`在每个原生detail事件前后及`start()`返回后检查`callback_errors`，任何错误中止账户。只读收据保留所有源事件ID、自终止事件、重叠/入场/退出审计，以及实际Trade.enter_tag绑定；源退出、原生-99%止损、强平、force_exit分列。

默认原生资金费必须另由独立审计重建所有边界订单、累计费用和逐时NAV。`FundingReceipts.py`仅提供边界清单和缺口毛暴露，不冒充独立审核。AXS/TRB的2026-06-24 04UTC记录仍未知；任何实际毛暴露使完整资金费经济结论保持UNKNOWN，不能补0、删币或用相反方向抵消。独立账户及组合审核将在root冻结其实现后执行；当前没有通过这些金融门槛的结果。

`plan.json` SHA256：`38cf9e9f9452e479c212fbe092f9f03f7b1ca2ee382867822c3a30ba10d265b8`。执行控制器为`execute.py`，但本阶段没有运行它。新金融结果、收益排名、独立OOS、真实历史规则认证、订单与部署均为0，持续目标尚未完成。

证据：`input_validation.json`、`precision_route_validation.json`、`preflight_attempt2.json`、`implementation_freeze.json`、`economic_protocol.json`、`runner_derivation.patch`、`completion_status.json`。
