# 加密价格路径连续性：原生因子检验

**两项预先冻结的交互候选均未通过预测门槛；本轮新资金账户、组合账户、已接受 alpha 和 bench 均为 0，账户净收益与 CAGR 未知。** 没有找到已验证的更高净收益，不将正 IC 或分组报价曲线当作完整账户。

当前目标为加密量化。此批与此前港美股输入扩展分开，未把任一市场结果作为另一市场证据。上一轮反转/ADX、MAX、Copula 等失败和未知状态保持原样。

## 源码、公式和本地假设

[PyTimeTK 原始源码](https://github.com/business-science/pytimetk/blob/246299cdd1d76b454cbccd0a614978eeb3cfbbae/src/pytimetk/finance/fip_momentum.py)固定提交 `246299cdd1d76b454cbccd0a614978eeb3cfbbae`、MIT 许可。全文阅读该文件，核对许可证和 README 的接口片段；没有声称审计全部工具库或把工具库称为盈利策略。代码和收据逐项保存。

源码对完整 w 个日收益计算：PRET=累计简单收益；B=上涨日比例−下跌日比例。original FIP=−PRET×B，modified FIP=sign(PRET)×B。持续上涨和持续下跌都可能得到很强的 modified 值，因此它本身不是做多方向信号。source skip_window 只屏蔽开头若干观察，不等同排除最近一个月；本轮设 0。

结果前冻结本地带方向交互 **S=PRET×modified=abs(PRET)×B=PRET×(−ID)**，正分数预测正收益；窗口固定为源默认 252 和文档多窗口例子的 63 个完整日收益。它允许离散跳跃与连续趋势具有不同预测方向，但不在看到本地结果后翻号。原始股票 FIP 的十二个完整日历月/六个月重叠实验，以及工具库指标，都不是这份加密周度策略合同。源原始/修改两种指标只做算术对照，没有把原始 ID 当无条件买入 alpha。

现有 Vibe core_equity_fip_id / core_equity_fip_pret 已核对：它们是已有股票研究定义，使用总回报与完整月份，并不等于本次 24/7 日线的 63/252 日报价交互。本轮不是重新发现股票 FIP，也没有全 Registry 独立 alpha 证明。

[issue #318](https://github.com/business-science/pytimetk/issues/318)的完整描述和维护者关闭评论已读。旧 Polars 多窗口闭包、跨证券收益以及 warmup 差异在维护者所列提交修复。这里仅隔离执行完整阅读的 `_compute_fip_series` 纯函数作对照，没有导入整个 PyTimeTK 或独立重现其三个后端/179 个上游测试。本地模块逐证券独立计算完整窗口。

## 不变的数据、时钟和标签

固定既有 40 个 Binance USDT 永续品种和原始顺序，目标账户窗口仍为 **2025-01-01 00:00～2026-08-31 23:00 UTC**。没有按结果删币、改窗口或重置年度资金；固定池也不是无生存偏差的历史全市场主表。80 个 1d/1h 原始文件的字节、完整网格与原生加载结果核对通过。实际日线有 1,796 个观测。

Freqtrade 固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`，595 个原始文件逐字节核验。新 `freqtrade/data/fip_path.py`、FipInputs、独立 Registry 桥均为明确的本地扩展。实际 StrategyResolver 加载 IStrategy 子类，DataProvider 供给价格；D00 行只能使用 D−1 日线完成后的特征，不读取当日日线高低收。保留 w+1 个价格才有完整 w 个收益的 warmup；零收益日计入分母，缺日/缺价不压缩、不填零。PRET=0 的源 sign(0)=0 约定保留，不冒称等同旧股票 ID 对零 PRET 的未知约定。

从 Jan1 起每 168 小时观察，跨年不重置相位；预测标签为 D01 OPEN 到下一周 D01 OPEN 的简单报价收益。完整标签 **86 个互不重叠周**，2025 入场 53、2026 入场 33；Aug26 最后决策的完整下周标签越过终点，明确保留为不完整。这里没有资金账户，不能把标签尾部处理解释为缩短真实账户窗口或净值计算。

原生预检独立核对 44,720 个标量，最大误差 9.81853487403e-16；与固定源纯函数比较 287,360 个输出位置（含 warmup 空位），核对 14,064,960 个小时映射单元。三次真实未来扰动/截断复算共 2,437,240 个特征单元不变。这些是数值检查，不能算成独立经济样本。

## 实际 Registry / factor_analysis 结果

当前拥有者仍为 Vibe 的 Registry、FactorAnalysisTool / factor_analysis_core；current HEAD 和工作区各文件 SHA 见 owner_versions.json。独立 zoo_root 只暴露实际 Freqtrade 特征，未修改全局 Registry。2 个交互、4 个同窗动量/符号比例对照及 lowvol63 共 7 个视图、3 个重叠全段/年度切片，实际运行 **21 次 factor_analysis**。

|分数|切片|周数|Rank IC|IR|正 IC 比例|冻结正向门槛|
|---|---|---:|---:|---:|---:|---|
|signed_path63|all|86|0.035414|0.1495|60.47%|拒绝|
|signed_path63|calendar2025|53|0.020711|0.0843|60.38%|拒绝|
|signed_path63|calendar2026_through_aug|33|0.059028|0.2639|60.61%|拒绝|
|signed_path252|all|86|0.076725|0.3023|65.12%|拒绝|
|signed_path252|calendar2025|53|0.073561|0.2759|64.15%|拒绝|
|signed_path252|calendar2026_through_aug|33|0.081808|0.3472|66.67%|拒绝|
|pret63|all|86|0.023620|0.0869|55.81%|拒绝|
|pret63|calendar2025|53|0.035548|0.1203|56.60%|拒绝|
|pret63|calendar2026_through_aug|33|0.004463|0.0192|54.55%|拒绝|
|pret252|all|86|0.111713|0.3843|70.93%|拒绝|
|pret252|calendar2025|53|0.133307|0.4346|73.58%|拒绝|
|pret252|calendar2026_through_aug|33|0.077031|0.2921|66.67%|拒绝|

冻结条件为 IC>0.03、IR>0.5、正 IC 比例>55%，三个切片全部满足，并在三个切片都优于同窗口 PRET、在指定对照中平均绝对相关<0.99。两个交互 IR 都不足，63 日也没有在 2025 优于普通动量，252 日全段和 2025 均弱于普通动量。2026 的局部改善不能用于事后选年、改方向或降低门槛。

lowvol63 全段 IC0.149919/IR0.5607 通过单个切片，但 2025 IR0.4691 未通过同一冻结门槛；它是既有控制，不因本轮主候选失败而转成新赢家。

独立排序、Spearman 与分组收益逐项对账：1,204 个 IC 行和 12,040 个分组 NAV 单元，最大分组误差 1.7763568394e-15。全段和年度复用相同 86 个周，不能宣称 1,204 个独立时期，也没有独立样本外。分组曲线无手续费、资金费、原生订单及现金约束，不是账户净收益。

每个候选与 16 个声明的同 40 币对照比较，共 32 项；其中包括源原始/修改指标、ID、另一期交互及前轮 MAX/MOM/低波动/偏度。与同窗 PRET 的平均绝对 Pearson 为 0.527482（63）和 0.658274（252），不触发 0.99 重复线，但相关较低不证明经济增益、机制独立或全目录去重。

## 资金边界与可复验结果

两个交互均在预测门槛拒绝，**不生成全现金账户冒充完成，也不建立事后挑选的资金组合**。原生输入策略拒绝 ft_bot_start、bot_loop_start 和入场。若未来有新的独立假设，仍须先按其自己的输入/预测合同，再走真实 Configuration/IStrategy/Backtesting.start/Trade/Order/Wallet/ZIP；登记或外部引擎不替代执行。Vibe hypothesis/configuration/SignalEngine/backtest/bench 本轮无新增运行，不把跨项目指标登记成官方策略结果。

原先 AXS/TRB 在 2026-06-24 04UTC 的资金费缺口、整段历史交易规格未认证等状态仍保留。报价标签不需要把缺失资金费当零，但净账户与组合收益不能因此被宣称完整；需要原生全钱包重跑及实际暴露检查，不能固定成交末尾加一笔估计费用就认证。财务计划的 1000/500 USDT、15/8bps、1倍多空及真实500+500钱包均只是未执行的冻结后续合同。

本地 14 项不同检查通过，生产模块完整 Ruff、研究脚本语义检查通过。原生/因子分析的工具会话终态和实际日志 SHA 在 execution_receipts.json；没有倒造未捕获的 OS PID、启动或终止时刻。上一轮 MAX 的192条和反转/ADX的452条封存内容重新核验未变。

## 多来源状态

Kim 的 SSRN6889877 仅取得主站搜索索引中的摘要，直接摘要/PDF访问403；摘要讨论14日rank-weighted PPC，但未取得精确定义或完整PDF。没有将本地符号比例改名为 PPC14、没有复现其样本或采用论文收益。

X、YouTube、抖音做了定向检索，未取得本轮可新增准入的完整原始 FIP 规则；关键词噪音、一般说明及宣传没有变成算法。既有社交来源状态以 source_intake.json 的原始 SHA 明确复用，没有新观看视频或把空字幕说成已读全文。

没有订单、部署、运行服务修改、提交或推送。没有新通过的独立 alpha、更高净收益、纸盘或实盘证明，整体目标保持未完成。

复验命令（仓库根目录）：

```sh
.venv-paper/bin/python -B research/crypto/fip_path_source_native_20261115_v1/verify.py
```

verify.py 只重读，不重跑经济筛选。preflight.py / predictive.py 对已有输出目录拒绝覆盖，失败记录与旧候选应始终保留。证据：protocol.json、protocol_freeze.json、native_preflight.json、predictive_results.json、predictive/、verification.json、goal_audit.json、artifact_manifest.json。
