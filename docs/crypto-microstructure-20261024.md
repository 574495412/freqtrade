# 永续合约盘口与成交流研究

2026-10-24。本轮为同账户自主策略补充真实的前瞻盘口与逐笔成交因子，重点解决小额层没有足够高净盈利概率的问题。前两批34个特征、6组组合的失败结论保留，见[条件因子研究](crypto-conditional-factors-20261024.md)。大额账户拥挤度与价格吸收仍只是待验证线索。

已经实现12个收到数据后才能使用的微观特征，完成三分钟真实试采及独立复核，并启动第一个固定一小时采集段。**本轮尚未计算新策略的前向收益标签、账户损益或胜率，没有策略取得实盘资格。** 当前进程以[统一状态](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/current_status.json)和实际进程为准。

## 数据源与时间口径

原生Binance连接器可以取得盘口与成交快照，但没有持续订阅能力。初次`websockets`探测受到macOS系统代理的可选依赖限制，失败回执保留。改用已有`aiohttp`及系统现有HTTP代理路径后，公开WebSocket连接成功；没有安装依赖或修改系统网络设置。

实际订阅六个固定USDT永续：BTC、ETH、BNB、SOL、XRP、DOGE。盘口使用`/public/stream`下的`depth20@100ms`，逐笔聚合成交使用`/market/stream`下的`aggTrade`。所有接收均为公开、只读数据，不加载交易凭证。

每条原文保存本机墙钟纳秒、单调时钟纳秒、接收序号、连接轮次和原始JSON。交易所事件时间与交易时间另行保留。因子按实际接收时间生成，不把晚到事件回填到过去。

公开普通盘口不含RPI订单；聚合成交的`q`包含RPI，`nq`为不含RPI的普通数量。因此普通成交流使用`p*nq`，总成交流使用`p*q`，两者分别记录。没有用总成交量冒充与普通可见盘口完全相同的流量。

本机与交易所时钟差使用三次实际HTTP时钟探测的发送／接收区间估计，之后每分钟更新。事件至接收的时间区间不是直接测得的单向网络延迟。

## 已实现的12个因子

协议为 [microstructure_plan_v001.json](../research/crypto/microstructure_plan_v001.json)，SHA256 `d5bb677baf57a1225e196b253a0c684eb6b89f10f86d84343dfc8a2d185de180`。源码在 [microstructure_features.py](../research/crypto/microstructure_features.py)。

| 因子 | 实际计算 |
| --- | --- |
| spread_bps | 最优卖价与买价之差除以中间价，单位bps |
| imbalance_l1 | 最优买卖挂单数量的有符号差占比 |
| imbalance_l5 | 当前可见前5档买卖名义金额的有符号差占比 |
| imbalance_l20 | 同口径前20档差占比 |
| microprice_deviation_bps | 用对侧价格按最优数量加权的微价格相对中间价偏离 |
| received_flow_30s | 最近实际收到30秒普通成交的有符号名义金额／普通名义金额 |
| received_flow_120s | 相同口径120秒窗口 |
| received_total_flow_30s | 使用包含RPI的总成交量计算30秒流向，单独报告 |
| received_rpi_share_30s | 30秒内总量与普通量名义金额差占比 |
| persistence_30s | 六个5秒普通成交流符号的均值；每个桶都须有正成交量 |
| flow_x_imbalance | 30秒普通成交流×前5档盘口失衡 |
| flow_x_microprice | 30秒普通成交流×微价格偏离 |

一秒形成一组状态。盘口超过一秒、最近成交超过五秒、成交ID断档、连接轮次改变或窗口未预热时，相关因子保持未知。时钟跳变保留并关闭完整性标记，不补零或插值。盘口为当前20档快照，没有将它描述为完整限价订单簿、排队位置或订单生命周期。

## 三分钟试采的实际结果

`microstructure_capture_pilot_v001`按登记的180秒自然结束，收到14,639条原始消息，形成1,074行一秒×币种状态。两条连接均只有一个连接轮次，无断线、原文序号缺失、成交ID断档、盘口ID倒退或墙钟跳变。

下表完整率在120秒预热之后计算；每币60行。完整因子仍只是数据可用性，不代表策略有交易机会。

| 永续合约 | 完整行数 | 完整率 | 可见价差中位数bps |
| --- | ---: | ---: | ---: |
| BTCUSDT | 59 | 98.33% | 0.01224 |
| ETHUSDT | 59 | 98.33% | 0.04153 |
| BNBUSDT | 59 | 98.33% | 0.13370 |
| SOLUSDT | 59 | 98.33% | 0.95579 |
| XRPUSDT | 59 | 98.33% | 0.76263 |
| DOGEUSDT | 56 | 93.33% | 1.25133 |

12条币种／来源流的事件至接收时间区间上界，p99约194–211毫秒；HTTP时钟往返约149–174毫秒。这里报告的是带时钟不确定性的区间上界，不是测得的单向延迟。短期无断线不证明长期稳定。

独立Decimal按原始盘口数量与逐笔成交重算60个真实状态、共720个因子值，最大差约`3.1e-12`。截短原文重新计算得到的846行过去特征，与完整原文计算的相应前缀一致。工程资格协议 [microstructure_quality_v001.json](../research/crypto/microstructure_quality_v001.json)在因子结果计算前保存，本次通过。

在当前研究每侧5bps基础手续费下，往返费用已经高于这些试采价差。因此先检验流向持续性、盘口确认和价格吸收是否能筛出较长的方向机会；不依靠未核实的maker返佣、碰价成交或排队优势。

## 事前登记的经济实验

新协议 [microstructure_economic_plan_v001.json](../research/crypto/microstructure_economic_plan_v001.json)，SHA256 `50e31c90c82d0f24e6696b1dd310330a3e1b0e50073f07844fe7996d2b2ba25e`。候选固定为三组，不根据已见结果调整阈值：

| 候选 | 固定机制 |
| --- | --- |
| ReceivedFlowControl | 30秒流向强度至少0.5，120秒同向至少0.2，六个5秒桶的方向持续度至少2/3 |
| ReceivedBookContinuation | 同一成交条件，加同向前5档失衡至少0.2和同向微价格偏离 |
| ReceivedBookAbsorption | 强普通成交持续但价格30秒变化不超过5bps，盘口与微价格偏向其反方向时研究反向机会 |

三者比较使用相同完整样本，价差不超过2bps，事件年龄上界不超过一秒。每个UTC分钟每候选最多一个六币提案，按两段成交流绝对值乘积排序，币种字母序打破并列。

标签计划为价格止盈／止损各0.4%、最长持有30分钟。入场及触发退出均留至少一秒决策与传输缓冲，使用之后实际收到的可执行侧报价：多头买ask、卖bid；空头卖bid、买ask。使用实际资金费事件和登记的标记代理；费用为每侧5bps加0/3/10bps三档。未知报价路径和资金费覆盖不按零处理。

这些报价标签尚未计算，也不是原生账户收益。只有至少30个成熟提案、扣费后正标签比例至少80%、三档平均净标签均正，才允许进入后续完整账户研究。小额候选与大额层都未取得资格前，原来的联合账户目标仍未完成。

一小时采集扣除两分钟预热和30分钟最坏持有期，最多不足30个成熟分钟提案。因此在计算任何收益标签前，另保存[固定入组回执](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_discovery_enrollment_v001.json)：发现阶段为两个依次采集、互不重叠的一小时段；第二段只在第一段实际结束后启动。每段单独预热、单独剔除末端，标签不跨连接断点。两个段全部结束前不选择候选，不延长采样直到出现成功。初始段已开始接收数据时保存该入组修正，未读取任何收益标签，明确保留这一时间关系。

发现阶段以后仍须新的不重叠前瞻验证。没有用本轮新数据把旧失败结果重新命名为样本外，也没有依据试采价格寻优。

## 运行与复算入口

采集器是 [microstructure_capture.py](../research/crypto/microstructure_capture.py)，分析器是 [analyze_microstructure.py](../research/crypto/analyze_microstructure.py)。原文每分钟压缩分块，关闭块有SHA回执；运行时每五秒更新状态，终止后冻结全部文件。断线生成新轮次，超过重连上限、字节上限或写入失败会停止并保留证据。

- [试采回执](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_capture_pilot_v001/receipts.json)
- [特征与复核结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_pilot_analysis_v001/result.json)
- [主采集登记](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_main_registration_v001.json)
- [第一小时实时状态](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_capture_main_v001/status.json)

启动第一小时的实际命令如下；已有目录禁止重复启动或覆盖。第二小时须使用登记的`microstructure_capture_main_v002`新目录。

```sh
.venv/bin/python -m research.crypto.microstructure_capture \
  --plan research/crypto/microstructure_plan_v001.json --profile main \
  --output user_data/crypto_quant/microstructure_capture_main_v001
```

当前未修改原生交易引擎，也没有启动新的交易策略、下单或生产部署。117项研究测试通过；新增测试覆盖实际接收时序、晚到消息、断线预热、成交断档、RPI数量口径、无效盘口和原文保存。

## 报价经济筛选实现进度

后续已实现完整的报价标签筛选入口 [microstructure_study.py](../research/crypto/microstructure_study.py)、[报价路径与成本](../research/crypto/microstructure_quotes.py)、[事前候选与排序](../research/crypto/microstructure_proposals.py)、[资金费回执校验](../research/crypto/microstructure_funding.py)及[独立标量／Decimal审计](../research/crypto/audit_microstructure_study.py)。当前仍未计算真实发现样本的收益标签。

实现保留以下关键约束：

- 两个登记段全部结束后，先冻结所有候选提案，再计算报价路径和净标签；缺少一段时不会开始经济筛选。
- 多头按ask开、bid平，空头相反；入场与触发退出后均留一秒缓冲，采用之后收到的报价。触发止盈后发生反转，也按之后的价格记录损失。
- 价格触发用原始价格的十进制比较。例如`0.07028/0.07-1`的浮点值略低于0.004，不能据此漏记精确0.4%触价。
- 已知盘口序号链断档、连接变化、无效报价、时钟或事件年龄失效，会使相应路径保持未知；不跨过已知丢包计算有利退出。
- 资金费必须有六币各自的完整、未截断、覆盖整个采集区间的公开查询回执。成功查询确认空结果与缺少查询严格区分，保留原始毫秒事件。
- 资金费处于开平仓时钟不确定区间时保留未知；实际持有事件按登记的同期可见mid代理计费。不会因账户以外的标记价格字段可得而事后改变已登记标签口径。
- 三档费用使用完全相同的已知路径；80%净正标签比例在每档检查，各档平均净标签均须为正。未知与末端剔除原因分别报告，标签不相加为账户收益。

合成的双段平价市场案例验证了点差与双边费用的损失，并由独立标量路径与Decimal重建48个净标签结果。这些是实现检查，不是实际盈利证据。研究测试现为152项通过，原有26项弃用提示保持记录。

代码与运行依赖已在真实收益计算前冻结到[实现快照](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/microstructure_economic_code_freeze_v001/manifest.json)。第一次采集已开始时完成实现冻结，这一时间关系明确保留；候选规则此前已经登记。经济筛选会拒绝与快照不一致的源码、依赖、协议或规范报价实现。

实际执行顺序为：第一段自然结束后立即开始已登记的第二段，同时只读复核第一段；第二段结束后复核、准备报价，再查询实际资金费。两个段均已落盘、所有候选提案均冻结后才读取收益。经济入口需同时提供登记的两个规范报价目录、完整资金费回执和上述实现快照；任何旧试采目录都不能替代发现样本。

```sh
.venv/bin/python -m research.crypto.microstructure_study \
  --plan research/crypto/microstructure_economic_plan_v001.json \
  --enrollment user_data/crypto_quant/microstructure_discovery_enrollment_v001.json \
  --segments user_data/crypto_quant/microstructure_quote_main_v001 \
             user_data/crypto_quant/microstructure_quote_main_v002 \
  --funding-receipt user_data/crypto_quant/microstructure_funding_receipt_v001.json \
  --code-freeze user_data/crypto_quant/microstructure_economic_code_freeze_v001/manifest.json \
  --output user_data/crypto_quant/microstructure_economic_study_v001
```

以上是数据齐备后的命令，目前尚未运行真实经济筛选。完整账户的两层贡献、实际费用与浮亏回撤、后续独立前瞻和可审阅上线版本仍未完成。

等待采集期间也核对了大额层的外部成熟机制。[2026年十币永续copula配对研究](https://www.aimspress.com/article/doi/10.3934/QFE.2026016)报告其中性版本在该文2021—2023年样本、0.08%往返费用下净收益为负。因此当前不优先增加copula选型与配对复杂度；该文是外部机制筛选参考，本项目尚未复现。审阅范围与决定保存于[来源记录](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/core_pairs_source_review_v001.json)。

## 一手来源

- [Binance官方Public流](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public)：20档盘口路径、事件字段和RPI可见性。
- [Binance官方Market流](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)：逐笔聚合成交、`q/nq`数量口径及主动买卖标志。
