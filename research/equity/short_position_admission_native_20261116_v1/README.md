# 港美股：周度淡仓机制的原始来源与原生输入准入

本轮新增机制线索是**周度须申报淡仓存量**，区别于既有日度卖空成交量。完成两个本地原生输入模块和 **32 项唯一检查**，但原始历史数据与方法横截面要求未准入；新增合格因子、factor_analysis、完整股票资金账户、组合账户和 bench 均为 **0**。港美股净收益仍未知，尚未找到更高可比收益，目标未完成。

沿用原始 **503 美股＋11 港股、2025-01-01～2026-08-31**，USD/HKD 分账；没有删名、扩池、缩窗、降横截面门槛或以加密收益代替。该输入队列不构成无生存偏差市场主表。

## 新来源学习与方法差异

固定 [wonglaitung/fortune@2036292](https://github.com/wonglaitung/fortune/tree/2036292bde004902285f358d761d5a00fb334dd3)，提交通过实际 git ls-remote 取得。完整阅读 scripts/sfc_signal.py（6655 bytes）；README 仅能力、SFC 数据、相关命令及许可段。原始文件使用固定提交 raw URL 并保存 SHA；没有声称 Git blob 对照或全仓审计。README 声明 MIT，但尝试的根 LICENSE 路径404，未取得确切许可文本；没有复制或执行来源代码、下载器、模型或资金引擎。

源脚本是描述性研究：强度＝周度申报淡仓 HKD 金额 / 报告日之前20个价格会话 close×volume 的均值（至少10期）；低强度减高强度方向。默认向后移10个价格会话，以生效日收盘到20会话后收盘为标签。每周20会话标签重叠，不可当连续账户收益；假设的10日滞后也不是每份报告真实历史可见时钟。源日历要求至少200个有价格名字，IC至少20个名字及3种强度，五分组至少25。**本地原始港股池只有11只，不能直接复用且维持源门槛。** 未据此否定源方法，也没有下调门槛凑出本地绩效。

[证监会原始索引](https://www.sfc.hk/en/Regulatory-functions/Market/Short-position-reporting/Aggregated-reportable-short-positions-of-specified-shares)说明的是达到报告条件者的聚合存量，并非全市场所有淡仓；平行柜台需注意原始柜台归并，缺收盘价金额可能为 n.a.。[原始发布公告](https://apps.sfc.hk/edistributionWeb/api/news/list-content?lang=EN&refNo=12PR94)明确报告与发布有时间差。报告日不能当可见日，也不能把未列示解释为零。

实际直连索引及2025-03-07原始CSV均HTTP403，保留错误HTML和状态，没有把错误页解析成行情。Web文本工具读取了CSV表头与选定前152个数据行，未取得完整原始字节和整段历史；这只是字段阅读。原始2012发布公告HTTP200、全文已读，可支持一般时间差，不能认证2025每周首次发布时间。GitHub API未认证限额403也保留，固定 raw 文件200与 git ref分别记录。

X/Twitter、YouTube、抖音定向检索没有取得本机制新增可完整核验原始规则；没有新观看视频或读取完整转录，不采用无关搜索摘录或宣传收益。逐项见 web_review.json 与 source_receipts.json。

## 实际输入与本地扩展

独立重新核验原始514份报价CSV及514份暂存Parquet的SHA；固定暂存字段没有 reported_short/short_position 字段。这仅是这批输入的库存审阅，不是断言市场不存在数据。没有将价格、成交量或缺列推导为淡仓0。

`freqtrade/data/equity_short_positions.py` 提供严格原始五列表头、日期、股数整数与金额 Decimal 校验。明确源零保留为“已申报存量零”，缺行未知，n.a.金额未知而股数可保留；重复柜台、日期混合、负数/非有限值、错误页、SHA错配拒绝。合成源格式夹具不冒充真实SFC文件。

`EquityShortPositionDataProvider` 继承既有本地 EquityDataProvider，后者继承固定上游 DataProvider。真实 `_set_dataframe_max_date` 约束回调；缺发布/观察钟不提供数值，当前首次取得不倒填过去。接口要求明确报告日，无自动旧报告回退；明确五位柜台及XHKG/HKD，不擅自跨市场、合股类或平行柜台映射。返回字典副本；报告修订需新合同，重复报告拒绝。没有新增因子公式或开仓规则，也没有把来源的HKD/ADV20量纲变成淡仓百分比。

填写源URL、SHA和时钟仅可验证输入一致性，不能认证原始真实性、历史first-seen、所有修订、全市场覆盖或报告经济有效期。后续因子还需单独制定真实可用性、时效、方向、标准化及持有合同。普通 `EquityBacktesting` 的历史拒绝边界保持不变。

## 验证与当前证据层级

32项检查覆盖份额/金额、明确零与缺行、缺价格、缺发布钟、当前取得不回填、重复柜台/修订、错日期/非有限/负值/不完整行/错误HTML、原始字节身份、市场/柜台身份，以及实际原生回调钟。发布前更改未来数量或删掉未来报告，回调结果一致；发布后才能读取数值，外部修改返回值不污染内部。全部为输入技术证据，无资金收益、CAGR或Sharpe。

固定上游 Freqtrade c051e9065c5b677f03817dd1f83cb980af1ed684 的595文件核对后，测试实际装入该树的DataProvider。本地两个新模块不是上游股票官方支持。项目 factor-research/strategy-dev-manager 与 Registry、factor_analysis、backtest、autopilot 的当前所有者哈希另列；输入未通过，尚未进入 Registry→实际factor_analysis→支持SignalEngine→完整原生账户及适用bench链。

首次采集缺bs4，改用标准库但保留403；未安装依赖。首次lint的导入/长行及冗余比较已修正，32项重跑不算额外样本。既有股息生命周期封存与加密月界封存逐项核对未变；本轮未运行股票资金账户、外部资金引擎或服务，没有下单、部署、提交或推送。

下一门槛是取得并认证原始周报字节、历史可见时钟和一个符合固定池/来源合同的方法，随后才做原生因子诊断；完整账户另需历史证券、名义价、整手/价位、费税、结算、公司行动与期末契约。技术通过与来源方法未知、经济未知分列，不用独立模拟器替代尚未完成的原生股票账户。

证据：[源方法审阅](source_method_review.json)、[514输入审阅](input_audit.json)、[所有权](ownership.json)、[验证](verification.json)、[协议](protocol.json)、[测试](tests_final.log)。
