# 港美股研究续轮：开盘成交接线与来源准入

**本轮尚未找到可比较的更高净收益。新增完整 Freqtrade 历史股票账户、资金组合、通过验证的独立 alpha 和 bench 均为 0；策略 CAGR、净收益和回撤未知。** 完成的是原生执行组件及来源审核，不能把合成夹具、预测分组曲线或其他引擎的历史账户排进收益榜。

真实研究范围仍为原始 **503 个美股、11 个港股，2025-01-01～2026-08-31**，USD/HKD 分开；此前拒绝的输入继续保留，没有按结果删证券、缩窗口、补零或改变旧候选方向。未下单、部署、修改运行服务、提交或推送。

## 所有权与官方流程

重新核对 Vibe-Trading 本地版本 0.1.18、HEAD `915f2713e2d5476a26663c69f32c5a9a1d4bea64` 的 AGENTS、factor-research、strategy-dev-manager、Registry、实际 `run_factor_analysis`、SignalEngine 脚手架与 backtest/bench 实现；工作区有既有本地修改，HEAD 本身不能代表全部当前文件，实现收据单列其 SHA。因子必须先通过输入与因果性准入，再经 Registry、实际 factor_analysis、受支持信号接口及完整原生账户、结果和 bench 读回。此次没有把未准入来源注册成可用因子，也没有用登记替代回测。

Freqtrade 原始实现继续固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`，逐字节核验清单 **595 个文件**；原始 crypto Backtesting、Trade/Order、Wallets 未改。以下均为**本地扩展**，不是上游官方股票支持。完整 `EquityBacktesting` 入口仍在原生初始化前拒绝。

## 实际原生开盘路径

新增 `freqtrade/optimize/equity_open_only.py`，组合既有交易日路由与费用成交 mixin。上一完整交易日信号在下一准确开盘使用；原生回调仍只能看到已收盘日线。传入实际原生 `Backtesting.backtest_loop` 的执行行只含当次开盘价，高、低、收全部等于开盘；指标和 DataProvider 中的真实 OHLCV 未改。

该模型只接多头现金、market 开盘单、普通已完成退出信号；原生 sizing、确认、Trade/Order 创建、成交与资金钱包仍运行。ROI、追踪/自定义止损、调仓加减仓、额外 custom_exit、限价、残留订单、跨时钟或非整手成交等被拒绝。原生若产生非普通信号退出（包括开盘跳空触发止损），本轮模型会拒绝该路径，不能把它忽略或称作已支持风控。它不认证集合竞价流动性、价差或滑点，也不支持收盘竞价或隔夜收盘买入模型。

实际初始化后发现旧成交 guard 要求 `ft_amount` 必须存在，但固定版原生回测创建订单只填 `amount`，不填 live placement 镜像 `ft_amount`。已在所属 `equity_execution.py` 允许镜像为空；非空不一致仍拒绝，实际数量仍经过原有整手、价格、现金及费用校验。旧版源码和首次失败保留。

## 已验证的范围

**276 项不同检查通过：23 项新增检查 + 253 项原有时钟、分红、净值、费用、成交、结算、历史输入、DataProvider 和入口回归。** 生产模块完整 Ruff 与本轮研究脚本语义检查通过。镜像为空/一致/冲突、非普通退出前拒绝、所有不支持配置及执行行隔离均被覆盖；重复运行不另计检查或独立样本。

六个主合成场景由真实 Configuration、ExchangeResolver、StrategyResolver/IStrategy、`Backtesting.__init__`、原生 `backtest`、未重写的 `time_pair_generator` 与 `backtest_loop`、原生 Trade/Order 运行：

|场景|市场|成交数|交易日|快照|
|---|---|---:|---:|---:|
|普通完整夹具|XNYS|4|7|14|
|普通完整夹具|XHKG|4|7|14|
|当日高低价反事实|XNYS|4|7|14|
|当日高低价反事实|XHKG|4|7|14|
|期末仍有持股|XNYS|3|7|14|
|期末仍有持股|XHKG|3|7|14|

共 **22 笔合成成交、84 个快照、42 个开盘回调**。独立审核不导入候选或资金组件，直接由订单、费用明细和结算标签重建 Decimal 收支、T+1/T+2 待收与可用现金、整股持仓及开/收盘 NAV，**504 个标量核对通过**。两市场反事实的全部经济字段、订单与回调精确不变；输入字节已变，mark provenance SHA 合理不同，不能要求来源哈希相同。持股场景按准确收盘估值，没有虚构期末平仓。

ABC/USD、ABC/HKD、1000 资金、1 股整手、价格、收费和日历都是明确合成夹具，**不是历史美港股/经纪规格**。港股首轮暴露缺 T+2 尾部结算日，后续只补 Jan13/14 的合成结算标签，保留 Jan2～10 七个交易日及原信号/报价/费用。未缩短账户窗口来规避。另一次美股技术重跑不计为第七个主场景。

这比此前直接调用生成器及手工构造订单前进一步，但**没有调用 Backtesting.start 的完整股票加载/统计/结果生命周期**。研究壳替换为明确股票 provider/wallet、保留期末持股及采集快照，完整生产入口仍关闭。原生默认单笔交易统计仍未纳入费用 overlay，不能当净绩效。

## 不同机制来源：行为注意力、南向持仓、板块轮动

新固定 [fortune](https://github.com/wonglaitung/fortune/tree/edeaf5535b9c60c70e1e52077414af3ec3b0b05e) 于 `edeaf5535b9c60c70e1e52077414af3ec3b0b05e`（提交时刻 2026-11-14 06:06:43 UTC），树有 1503 个路径。核对 README 的范围、口径及许可说明，阅读行为计算、南向数据获取/按日特征接口及板块轮动相关实现；另保存月度股本获取脚本与验证文档，尚未完整审阅这两份文件，不声称审计全仓。README 声明 MIT，但树中无 LICENSE，直接 LICENSE 请求 404；没有采用其代码进入生产模块。

**行为注意力。** 审阅后仅隔离执行两个原始纯计算方法体，没有执行仓库导入、下载器、pickle 或交易入口。源 `STR_Signal` 用整段单证券历史的 `rank(pct=True)` 加权，再 shift(1)。对同一合成过去分别截到 40/60/80 行，与包含未来的 120 行实际源方法比较，共 21 个字段/前缀检查：仅 STR_Signal 改变过去值，三个前缀分别有 18/38/58 个共同有限值变化。滞后一天没有消除全历史排名的未来依赖。停止该信号的准入，没有改成 expanding rank 然后冒称复现原方法。其他字段在该夹具上前缀一致，不等于经济有效或完整算法正确；没有注册、IC、账户或收益。

**南向持仓。** 固定 CSV 的 Git blob SHA 与树逐字节匹配，实际 117505 行/262 证券与作者 manifest 一致。原池十只普通股各有目标窗口 393 个日期，2800.HK 在来源中完全缺失。最初相对 408 个港股交易日列出 15 个未观测日期；随后独立 CSV 重建和港交所 PDF 具体单元格审核确认：**这 15 日全是正常南向休市，不是已经证明的抓取缺测。** 覆盖差异必须按通道日历解释，不能用缺测率或填零制造因子。

[港交所 2025 日历](https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Reference-Materials/Trading-Hour,-Trading-and-Settlement-Calendar/2025-Calendar_pdf_e.pdf)两页及 [2026 日历](https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Reference-Materials/Trading-Hour,-Trading-and-Settlement-Calendar/2026-Calendar_pdf_e.pdf)第一页已渲染、目视核对；收据保存 15 个 Southbound Trading / Closed 的页码、文字框坐标及来源 SHA。初始日历解析误把标题里的 Dec 当月份行，以及过严垂直截点的失败日志均保留。

但最新 Git 快照、观测日期和月度报表的 filing_date **仍不是历史发布时钟证明**。月度股本表也不能替代完整分红/拆股/退市/供股等事件账。2800.HK 不补零，普通南向休市也不自动前填数据或映射成零流量。该机制尚未进入预测或账户准入；不是经济否定。

**板块轮动。** 源脚本使用当前板块映射和分段 forward simple return 求和，无完整资金/费用/公司行动账。本轮只阅读方法，没有执行外部账户引擎、接受其收益或冒充 Freqtrade 结果。

## 社交来源状态

继续检索 X/Twitter、YouTube、抖音，未取得可新增准入的完整策略及账户证据。此前 X 原文和 YouTube 作者描述/字幕轨道状态明确标为复用，不冒称本轮新阅读或观看。未观看视频；此前字幕正文为空的事实保留。本轮新抖音链接索引为一般量化教育，直接 HTTP200 的 1072 字节实际是 4XX 风险拦截页，不是视频规则。各渠道状态及既有来源 SHA 见 `source_intake.json`。

## 下一段必须完成的工作

完整股票 constructor/load/start/净结果/bench 接线、历史证券与名义报价单位、整手价位、经纪税费适用性、股息/其他公司行动/退市及实际来源时钟仍未齐。做空来源另需借券、召回、空头股息和双腿资金合同。

准入后因子才进入原生 factor_analysis；通过者及既有基准才在同市场、币种、原始资金、完整窗口、成本税、结算、行动和期末合同下生成账户。组合需实际分配资金的钱包，不能缩放/拼接曲线。这轮没有收益冠军、独立样本外或部署证明，整体目标尚未完成。

证据：[验证](verification.json)、[独立轨迹审核](trace_audit.json)、[源方法因果性](source_causality_audit.json)、[持仓覆盖初查](hk_flow_input_readiness.json)、[官方日历对账](connect_calendar_reconciliation.json)、[技术修订](technical_amendment.json)、[实现与版本](implementation.json)、[来源状态](source_intake.json)。
