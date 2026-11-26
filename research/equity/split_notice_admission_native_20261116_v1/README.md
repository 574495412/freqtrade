# 港美股：拆股公告修订研究与 Freqtrade 原生输入

**本轮完成 38 项输入/边界检查、4 个源码分支探针；新增合格独立因子、factor_analysis、真实历史股票完整账户、组合账户及 bench 均为 0。尚未证实更高可比净收益，整体目标未完成。**

保留既有 **503 美股＋11 港股，2025-01-01～2026-08-31，USD/HKD 分账**合同。独立重新核对 514 份原始 CSV 和 514 份暂存 Parquet 的 SHA，没有删证券、缩窗口、改权重，也未将加密结果计入港美股。这批输入仍不是已认证无生存偏差市场主表。

## 来源与实际学到的差异

固定 [QuantConnect/Lean.DataSource.EODHD@cfc3306](https://github.com/QuantConnect/Lean.DataSource.EODHD/tree/cfc33067942ae481f3015f2953fc978e10db8392)。实际 git ls-remote 取得提交，保留固定 raw URL、状态和 SHA；未声称 Git blob 对照或全仓审核。完整阅读选定 Python 示例、C# 数据适配器及 README。源码头声明 Apache-2.0，另保留 Apache 原始许可全文；两个猜测的仓库根许可路径404，不把错误页当许可。

这条来源的机制线索是**拆股前的关注/需求预期**，不等同于拆股当天的持股单位调整。它也不证明相对已有全部因子的独立性。

|选定来源|实际规则与限制|
|---|---|
|固定 Python 示例|AAPL；日期设置为2021-10-25～30。split_date 不晚于当前时刻＋3日，old/new 比率小于1时目标多头1，大于1时目标空头−1。没有采用其仓位或作者收益。|
|固定 C# 适配器|SplitFactor 是旧股数/新股数；输入文件日减1为 Time，EndTime 再加1日。原字段是SID、ticker、拆股日、optionable、比率，不提供逐份公告的首次观察证据。|
|[当前数据文档](https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/eod-historical-data/upcoming-splits)|选读数据说明与当前示例2：两日正向信号、等权组合、10%现金预留。这与固定 GitHub 单标的示例不同；不混称为同一原策略。|

仅提取已读源码的 OnData 方法，在记录函数调用的桩对象上执行4个纯语义探针，没有初始化 QuantConnect、加载下载器或运行资金引擎。探针确认：

- 正向拆股比例产生目标多头调用，反向产生目标空头调用；后者另需真实借券账户合同。
- 目标数据缺席且已持仓时立即调用清仓，并没有明确的“拆股后一天”计时器。
- 只写了日期上界；若数据源继续送入过期拆股日期，仍可产生目标多头。实际数据源是否会这样送入未验证，不能据这个反事实断言原策略历史发生错误交易。

这些是分支语义，桩调用不是订单、成交或收益；没有验证完整QC初始化/API兼容性、历史数据交付或来源账户绩效。当前网页说明也不能认证2025历史first-seen。

## 真实输入证据

[ORLY 原始发行人公告](https://corporate.oreillyauto.com/2025/03/13/oreilly-automotive-inc-board-of-directors-approves-15-for-1-stock-split-subject-to-shareholder-approval-of-an-increase-in-the-number-of-authorized-shares-of-common-stock/)取得HTTP200原始字节：15拆1仍附股东批准等条件，6月2日登记，6月9日收盘后分派，6月10日开始按拆股后单位交易。三个时间不能互相替代。[后续SEC 10-Q](https://www.sec.gov/Archives/edgar/data/898173/000089817325000049/orly-20250630x10q.htm)选定段落确认6月10日实施；SEC两份直连均HTTP403，原错误字节保留，网页文本读取不算原始文件下载成功。

既有原始ORLY CSV 的6月9日收盘约1348.10，6月10日开盘90。这是重要的价格单位边界，不能直接解释成账户暴跌或事件alpha。本轮没有改价、补事件或变更持股。514份CSV都没有本模块所需的拆股公告/修订/可见时钟字段；这只是该批输入库存结论，不是断言公开市场不存在资料。

真实公告字节可以绑定手工阅读的条款，但缺少认证的精确历史观察钟。实际将该原始字节绑定到新模块并查询2025时刻，返回unknown、比例为空；没有因为今天取得旧公告就倒填历史信号。没有取得完整日度upcoming-split数据、全部证券的事件修订或原始港股历史拆股输入。

X/Twitter定向检索没有取得可用原始策略帖。YouTube追到[OIC原页](https://www.optionseducation.org/videolibrary/corporate-actions-forward-splits)与[原片](https://www.youtube.com/watch?v=HZJ39YmjOMY)，只读目录/时间戳，视频抓取受限，未观看或读取转录。抖音只读[检索页](https://www.douyin.com/shipin/7620282807100639286)相关视频的中美量化差异AI文稿，未核音频，没有可执行拆股规则，不采用其中市场占比或宣传收益。具体范围见source_intake.json。

## 本地原生扩展与验证

新增 `freqtrade/data/equity_split_notices.py` 和 `equity_split_notices_provider.py`：

- 不可变原始字节、来源URL/状态/SHA、明确证券/事件/修订身份；比例用精确分数，旧/新价格因子与新/旧数量因子互为倒数，不给15拆1颠倒方向。
- 原生DataProvider回调时钟必须同时到达发布钟和观察钟。最新**已可见发布**胜出；迟到的旧文不能覆盖已知修订，未来修订不能改写过去。
- proposed/approved/cancelled分列；可见撤销清除条款，缺行未知。登记、分派和计划首个交易开盘独立；超过计划日仍不自动认定实施。
- 精确USD/XNYS或五位柜台HKD/XHKG绑定，不跨证券类、币种或事件自动回退。香港案例只认证合成接口，不认证历史香港事件。

38项检查覆盖真实继承回调、未来比例扰动/截断、迟到旧文、撤销、空钟、当前取得不回填、时区/数值/身份/源字节错误、返回值隔离与不可变性。普通EquityBacktesting在原生构造器调用之前仍明确拒绝，未初始化钱包、交易所或订单。

固定上游Freqtrade `c051e9065c5b677f03817dd1f83cb980af1ed684` 的595文件逐项核对，测试实际装入该树DataProvider。本轮模块是本地扩展，不是上游官方股票支持。当前Vibe Registry、factor_analysis、hypothesis/config/SignalEngine/backtest/bench所有者另存哈希；技能文档相对前轮已变化，记录当前版本，未用旧哈希代替。股息生命周期377路径及淡仓准入31路径封存分别逐项核对未变。

生产模块Ruff通过；研究脚本仅豁免用于测试/审核断言的S101和启动原生树后导入的E402，源码探针exec有单行明确说明。第三方源不参与格式或lint。首次测试语法错误与lint记录保留；一次递归格式检查误触源Python后，保留误改副本，按固定提交下载并匹配原采集SHA恢复，4个探针重跑一致，不增加独立样本数。没有安装依赖。

## 尚未完成的收益链

该模块不改持股、现金或订单，不支持拆合股持仓变更、零股现金、due bills、挂单变更及借券。真实全池公告数据与首次可见性未准入，因此本轮没有新增Registry定义或factor_analysis；不把单个公告或4个探针当作完成的选股研究。

后续须先取得完整原始数据并冻结与来源一致、或明确标为本地适配的方向/持有合同；因子经Registry→实际factor_analysis，组合经受支持信号接口→完整Freqtrade资金账户，检查实际运行状态、成交、现金、股息、公司行动、期末持仓与净值，再做同市场、币种、资本和完整窗口的净收益比较。适用Vibe流程时还需真实hypothesis/config/SignalEngine/backtest/bench，不以登记替代执行。

**目前股票净收益、CAGR与新组合增益仍未知；没有下单、部署、服务变更、提交或推送。**

证据：[协议](protocol.json)、[源码审阅](source_method_review.json)、[语义探针](source_witnesses.json)、[输入审核](input_audit.json)、[验证](verification.json)、[所有权](ownership.json)、[测试](tests_final.log)、[清单](artifact_manifest.json)。
