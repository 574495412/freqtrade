# 期权波动风险溢价：真实合约、历史柱与交割输入

**55项边界检查通过；1,949条原始合约元数据、25个历史小时柱起点及真实交割日价格进入本地原生输入路径。独立审核通过。新增完整期权资金账户、组合账户、接受的独立alpha、factor_analysis及bench均为0，尚未证明更高可比净收益。**

本轮依当前活动目标继续加密研究；上一轮港美股拆股扩展单独保留，不计入加密收益。原有可比账户参考窗口仍是2025-01-01 00:00～2026-08-31 23:00 UTC，没有缩成这次单合约样本来宣称完成。BTC/ETH当前期权列表也没有倒用为2025历史证券池。

## 来源研究与不能采用的收益口径

固定[mikemarshall01/crypto-quant-strategies@6f039e6](https://github.com/mikemarshall01/crypto-quant-strategies/tree/6f039e64d6d13227cdc5a79bbd9d78b093468cd0)，读取notebook08的选定数据/回退、方差差额、月度roll、成本、反向结算局限及结尾段。原始86,002字节文件及SHA保留，MIT许可从前轮封存字节复用并核对。没有宣称完整阅读全仓或所有引用文献，未运行其完整notebook、下载器或资金引擎。

它研究的是卖出波动保险/短gamma的风险溢价，与此前单纯价格趋势或宏观降仓机制不同。但原作者明确将收益写成归一化方差差额，VEGA_SCALE=100不是资金账户的初始现金，也不是实际某期权的vega；DVOL曲面指数不是某张ATM call/put的可成交权利金。未来月份的realized variance是事后测量，不能当入场已知因子。原始标明synthetic的回退没有被采用为历史数据。

仅提取6条已读算术赋值，在合成输入上执行两类语义探针，无网络或原作者账户：

|合成探针|实际结果与边界|
|---|---|
|同一implied=.7、realized=.6、abs_move=1，scale100→200|单roll归一化net约11.87→23.74，数值随任意scale加倍；不是USD或币本位账户收益。|
|net依次−1、−2，使用原始cumsum/DD语句|原式DD为0、−2；若包含期初零锚则为−1、−3。仅证明该语义边界，不把合成损失当原作者历史结果。|

原文将反向期权概括为“struck and settled in BTC”；[场所原始说明](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options)与实际元数据区分USD行权价、币权利金、币结算及基础币数量。此差异必须在账户中解决，不能把方差差额或DVOL空头当真实卖期权收益。

## 原始API实际取得了什么

|原始资料|实际取得|尚不能证明|
|---|---|---|
|当前BTC/ETH期权元数据|1064＋884条，另1张过期BTC call，全部逐条解析/独立核验|历史全期合约主表、当时首次可见、历史最小量/费用或交易状态。|
|BTC-27JUN25-100000-C图表|2025-06-01 00:00～06-02 00:00 UTC共25个bar-start，21根volume=0、4根>0|bid/ask、可成交量、可按每根close成交或完整策略窗口。最后一根小时柱在06-02 01:00完成。|
|相同区间公开成交查询|首个含未文档化include_old参数的请求保留；另按文档字段查询也为0条、has_more=false|不能因此断言整个市场当时无成交，图表与成交带差异未解释，未补零或伪造逐笔。|
|BTC原始交割指数|精确日筛选取得2025-06-27一条、records_total=1，107106.02 USD/BTC|入场权利金、真实持仓、对冲成交、私有费用或完整账户收益。|

首个不筛日期的delivery请求count=1000，实际只返回100条、records_total=2670；没有把短页当全部历史。随后使用原始[文档的UTC日期筛选](https://docs.deribit.com/api-reference/market-data/public-get_delivery_prices)，取得目标单日。这是该次响应差异，未推定所有端点或历史版本都如此。

产品/费用支持页直连HTTP403原字节保留，选定内容通过web文本核对，不冒充下载到完整原始文件。其它成功JSON都有URL、状态、捕获时间和SHA。当前捕获时间晚于历史窗口；没有倒填为当时first-seen。

## 本地原生扩展

新增3个代码所有者和一份模块文档：

- `freqtrade/data/inverse_option_contracts.py`：严格原始JSON-RPC/请求身份，USD strike与币premium/settlement、明确08UTC expiry、数量/价位、单日交割输入、显式费用参数及孤立到期内在价值。
- `freqtrade/data/history/inverse_options.py`：固定上游FeatherDataHandler→load_pair_history，原始完整小时网格在原生去重/补齐之前校验，显式不补行、不补成交量。
- `freqtrade/optimize/option_backtesting.py`：完整期权账户未支持时，在原生构造器、交易所、钱包和订单之前拒绝。

BTC/ETH此类inverse option的支持乘数固定为1，API订单amount是基础币敞口；不能套用反向期货的USD数量。[lot_size为保留字段](https://support.deribit.com/hc/en-us/articles/38507223482781-API-guidance)，不是交易下限或步长。实际ETH首条lot_size=10而min_trade_amount=1，新检查不会错误要求10。价位按原始[严格above_price规则](https://support.deribit.com/hc/en-us/articles/25944779257373-6-July-2023-TS)：.005仍用.0001，.0055用.0005。这只是已知约束检查，不认证订单接受、数量步长或历史规则。

费用函数要求显式给出underlying_rate与premium_cap，不默认为全历史当前费率。以当前标准文档参数3bps/12.5%作合成算术：.1基础币敞口、premium=.001时手续费.0000125币；premium=.01时.00003币。[费用页](https://support.deribit.com/hc/en-us/articles/25944746248989-Fees)中的账户等级、组合减免、交割费、舍入与真实历史费率没有被这些例子认证。

当前产品说明明确2026-08-01起到期ITM反向期权先生成同到期反向期货，再以币结算。代码对日期分支和USD期货名义量做孤立算术检查；已有到期期货净额可能改变delivery fee，因此没有把孤立gross intrinsic当多腿账户现金。跨日期及call/put/正负仓位案例均明确为**合成变体**，并非认证这些变体在场所实际挂牌或持仓。

真实交割输入绑定了原始指数、到期日、单条/total完整性及观察钟。假设1BTC标的敞口计算出约.06634566 BTC毛内在价值，仅为单位演示；没有实际开仓，也未知premium/hedge cash，绝不是账户盈利。

历史存储键`instrument_name/base_coin`及SPOT CandleType仅使用已有OHLCV存储命名空间，不表示支持CCXT现货交易该期权。25行、21个原始零成交量逐值保留；源`cost`是报价币成交额，不是手续费。[图表API](https://docs.deribit.com/api-reference/market-data/public-get_tradingview_chart_data)还明确不含block/RFQ，不能与逐笔、mark、bid/ask混用。

## 验证、所有权与保留项

实际装入固定Freqtrade2026.9 `c051e9065c5b677f03817dd1f83cb980af1ed684`，核对其595个文件及真实handler/loader路径。本轮是本地扩展，普通crypto回测路径和TradingMode枚举未变，不声称上游已有期权资金账户。

55项检查覆盖单位/方向/到期边界、fee cap、tick阈值、源JSON/请求/日期错误、ETH数量、原始重复行在原生清洗前拒绝及未完成资金入口拒绝。独立脚本不导入候选模块，重新读原始元数据、图表和交割响应，以Fraction核对1,949条元数据及162个图表/单位标量；非终止币小数仅允许明确显示精度误差，不增加收益。审核的是输入和孤立算术，没有重建资金账户。

先前43/49项技术结果与日志保留，加入真实交割输入后的最终55项不是额外55个经济样本。原有metadata、history及unit_probe输出与保留版本逐字节一致。原生/研究代码Ruff通过，第三方sources目录排除格式和lint；研究断言/启动后导入有明确S101/E402豁免，选定源码算术执行有单行S102说明。初始lint问题和未文档化参数探测记录保留，没有安装依赖。

当前Vibe factor-research/strategy-dev-manager及Registry、factor_analysis、hypothesis/config/SignalEngine/backtest/bench所有者重新读取/留哈希。此轮输入未达到可执行策略研究合同，故不登记新因子、跑虚构IC或登记外部绩效。完成原生资金账户仍需支持期权头寸、币premium/collateral、保证金/清算、对冲实际成交、历史费用/交割净额、期末持仓及统一货币NAV；需要的横截面因子另须Registry→实际factor_analysis，适用Vibe链时需真实SignalEngine/backtest/bench。

宏观资金研究398路径、宏观源研究43路径、港美股拆股50路径封存逐项核对未变，各自失败与范围保持独立。最初复查AXS/TRB的2026-06-24预期04UTC资金费记录仍缺，不认定零费，也未改变低波动候选的未知结算结论。

X/Twitter、抖音定向检索无新增可完整核验规则。YouTube追到[Deribit原始配文](https://insights.deribit.com/education/introduction-to-deribit-metrics-page/)及[嵌入视频](https://www.youtube.com/embed/juV9muSVPNw)，配文已读，视频抓取受限，未观看/读取转录，不采用宣传收益。没有订阅实时流、启动服务、下单、部署、提交或推送。

**当前只有来源/原生输入进展，期权净收益、完整账户与组合增益仍未知。下一项实质门槛是原生资金生命周期与完整历史输入，不能用归一化方差差额替代。整体目标保持未完成。**

证据：[来源方法](source_method_review.json)、[原始回执](source_receipts.json)、[元数据准入](metadata_admission.json)、[原生历史](native_history_admission.json)、[真实交割](actual_delivery_admission.json)、[单位算术](unit_probe.json)、[独立审核](independent_audit.json)、[验证](verification.json)、[所有权](ownership.json)、[清单](artifact_manifest.json)。
