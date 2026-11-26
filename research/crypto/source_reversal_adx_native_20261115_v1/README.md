# 原生策略来源与多策略账户验证：反转 × ADX

**14 个实际原生加密账户完成并独立对账；新策略与组合全部亏损，候选拒绝。此批不属于港美股证据，也未发现可推广的更高净收益。**

GitHub 来源为 freqtrade/freqtrade-strategies，固定提交 `f3340ce11f5bdf62f598522e64d1f5638eaa13f5`，完整读取 Strategy002.py、ADXMomentum.py、README 和 GPL-3.0。这是官方项目托管的社区教学示例；作者未提供本批同口径账户，README 不保证盈利。两个原始文件逐字节保留。

本地组合把 5 分钟反转入场与最后完成的 1 小时原始 ADX long 信号取交集，使用 native merge_informative_pair；保留反转退出、ROI、止损及利润限制。另用真实各 500 USDT 钱包组成两个静态组合，没有缩放曲线、转账或重置。既有八币顺序在结果前冻结，不是按本批收益挑选。

完整共同窗口：2025-01-01 00:00 至 2026-08-31 23:00 UTC，八币 ADA/BNB/BTC/DOGE/ETH/LINK/LTC/XRP，USDT 永续、多头、1 倍逐仓、最多八持仓。每侧 15/8 bps 用原生费率表示手续费加滑点代理，资金费另计；不是订单簿成交成本模型。源示例现货 → 本地永续和 1000 根预热是明确改编，非作者完整账户复现。

|1000 USDT 账户|15 bps 净收益|8 bps 净收益|15 bps 小时回撤|
|---|---:|---:|---:|
|SourceReversal|-23.12%|-18.36%|25.84%|
|SourceADX|-58.07%|-42.28%|63.19%|
|ReversalInADXTrend|-6.26%|-6.04%|7.00%|
|HoldEight|-44.23%|-44.12%|66.99%|

组合每个成本各自由两条真实 500 钱包逐时相加：

|组合|每侧 bps|全窗净收益|小时回撤|
|---|---:|---:|---:|
|SourceReversal_plus_SourceADX_15bps|15|-38.98%|43.00%|
|SourceReversal_plus_ReversalInADXTrend_15bps|15|-13.76%|15.29%|
|SourceReversal_plus_SourceADX_8bps|8|-29.19%|35.09%|
|SourceReversal_plus_ReversalInADXTrend_8bps|8|-11.85%|13.56%|

静态组合终值是同本金各腿收益的凸组合，不能预设超过最优腿；低相关也不是新增 alpha。500 与 1000 钱包存在整量、最小名义金额和可用资金差异，未用等比缩放替代。SourceADX_500 每个成本各有 23,472 次原生最小额拒绝，不能把同数目拒绝理解为相同资金路径。

交互策略两个成本均优于三个控制，但全段、2025 和 2026 年切片均亏损，日 NAV 夏普为负，冻结门槛失败；不翻方向、不改阈值、不重调权重，也不提升为纸盘或实盘。所有账户全窗及连续年度切片、CAGR、夏普、原生终点强平数字分别保存在 results.json。2025 末与 2026 初共用同一净值边界，年度收益可乘法还原全段。夏普只用完整 24 小时 NAV 比率，末尾 23 小时仍计入收益与回撤。

实际引擎固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`：Configuration / StrategyResolver / IStrategy / DataProvider → Backtesting.start → 原生 Trade / Order / Wallet → 14 个 ZIP。固定 595 个原始文件逐字节核验。没有新建独立资金模拟器替代回测；后验 Decimal 与 mark 重建仅为独立审核，也没有把这些账户登记为 Vibe SignalEngine / bench。新增 factor_analysis 和 bench 均为 0。

原生按整月报价推断小数精度会读取未来报价。资金运行前以 precision_amendment.json 保留旧计划和源码，新增本地 FrozenPricePrecisionMixin，执行只用结果前冻结市场 tick。原始引擎未改，当前规格也不能认证 2025–2026 历史规格。源公式、阈值、ROI、退出与成本未因该修订改变。

32 个输入网格完整；每币 1,824 个预期资金费事件。1,533,568 行源指标/信号一致性、192 个原生未来扰动/截断回调、1,408,832 个信息时钟单元通过。独立核对 11,011 个原生交易生命周期、22,022 笔成交、204,288 个小时 mark 净值点，最大净值误差 2.8704801025e-07 USDT。每笔价格/数量 tick、原生槽位预算、信号 SHA 与预检一致性通过。18 项不同测试通过（16 执行/资本检查及 2 报告窗口检查）；新报告测试首次相对导入失败保留，修复测试加载方式，没有重跑或改资金结果。未修改 GPL 源的 qtpylib 弃用警告。

资金费按所固定原生 exchange.calculate_funding_fees 的开/平时间双端包含约定逐事件重建。没有双生命周期重复归属事件。ADX_1000 的固定成交排除入场同刻资金费敏感性为 +1.3421/+1.7417 USDT（15/8bps）；这只是固定成交的现金归因，不是改变结算规则后的完整账户复跑。私有结算先后、历史交易规格、逐笔滑点及独立样本外均未认证。

X、YouTube、抖音沿用上批明确绑定 SHA 的状态，没有把复用元数据、空字幕或平台自动摘要冒称本轮新观看或完整策略。保留旧 MAX、Copula、资金费缺口与全部失败证据。没有订单、部署、服务修改、提交或推送。港美股完整历史账户准入继续独立推进，本批加密亏损不代表港美股结果。

证据：protocol.json、protocol_freeze.json、precision_amendment.json、preflight.json、execution_complete.json、analysis_final.log、results.json、verification.json、artifact_manifest.json；实际 ZIP / 订单 / 钱包轨迹位于 plan.json 的 output_root。
