# Copula 双腿原生成交、资金费与净值验证

**已完成六类合成夹具的完整 Freqtrade 回测链和独立核对；原始二十币种 Copula 的资金账户仍未准入，收益与组合改善继续 UNKNOWN。** 没有发现新的可比收益冠军，也没有订单、服务启动/重启、部署、提交或推送。整体研究目标保持未完成。

延续并重新核验已封存 v3 的 **2197 个路径、229054356 个逻辑字节**。原模型、20 来源成员、88 个滚动周、三档来源阈值和失败记录未改。真实账户目标仍是 **2025-01-01 00:00～2026-08-31 23:00 UTC，14592 个共同小时、当前小时所有成交/资金费事件之前的 OPEN mark**；native 强制终点另列。本轮 50 小时合成测试不是该完整经济窗口。

## 实际跑过的原生链

固定 `c051e9065c5b677f03817dd1f83cb980af1ed684` 的 595 个上游文件。实际经过 Configuration、StrategyResolver/IStrategy、DataProvider、**Backtesting.start**、原生 Trade/Order/Wallets 及实际 ZIP，而非只注册结果或独立模拟账户。上游 matcher、资金钱包和原始源码没有修改。

本地 `CopulaPortfolio.py` 通过原生回调执行封存的双腿意图：普通决策 T+5 分钟，周界旧仓 T+5 平，再在真实空仓的 T+10 取预算并进入新周。实际原生 `get_total_stake_amount` 已应用 .99，只用一次。两币 β 使用同一倍数、分别原生向下取数量，周内重入不重新定数量；保证金杠杆 3，不把它误作目标毛额 3。

两腿同时通过数量、最小/最大金额与联合保证金/入场费用检查后，才允许请求。custom stake/confirm 出错显式返回 0/False 并保存 fatal，避免原生安全包装器使用默认 stake/True。真实持仓在后续原生回调逐项检查数量和方向；不一致的账户作废。

**这不是原子双腿成交保证，也没有实现单腿补救或回滚。** 负例第一腿成交、第二腿拒绝后，在下一根 5 分钟回调检测不匹配；该合成负例保留单腿直到 native 终点，整个账户被拒绝，不能引用其收益。未用补单、止损或参数调节救回它。后续真实资金 runner 必须把 fatal 和完整审核作为失败条件，不能只看 native 返回码 0 或 ZIP 存在。

## 六个预先冻结的合成场景

FAKEONE/FAKETWO、.1 数量步长/最小数量、.001 价位、5 USDT 最小名义额、每侧 15bps、价格与每小时资金费均为**明确合成规格，不是 EOS 历史规格**。区间 2025-01-02 00:00～2025-01-04 02:00（终点不含），共 50 个小时；每次 native 循环实际检查 599 个 5 分钟时钟，fatal 负例除外。

|场景|实际本金|原生 trades/orders|独立审核结论|
|---|---:|---:|---|
|normal1000|1000|10 / 20|合成执行一致|
|normal500|500|10 / 20|实际独立钱包；不是前者曲线的一半|
|under_minimum|1000|0 / 0|第二腿门槛过高，实际两腿均未成交|
|one_leg_rejected|1000|1 / 2|00:10 检出单腿；账户拒绝|
|missing_funding_exposed|1000|10 / 20|缺事件前实际持有 4.2 单位；账户拒绝|
|missing_funding_flat|1000|10 / 20|缺事件前实际空仓；缺口仍保留为 UNKNOWN|

共 **41 个原生 trade 生命周期、82 个实际成交订单、300 个小时主观察净值点、1200 个独立净值标量检查**。不同技术重跑不另算独立候选或金融样本。

主观察复用固定 SHA 的既有 read-only native observer 及小时捕获钩子：在原生当前小时第一个事件产出前读取实际 Trade、已实现资金状态和 mark，保留全部原生事件原序；49 个实际小时捕获加 1 个已知初始空仓点。预算及终点余额另从实际 Wallets 读回。OPEN 估值含已发生成交的费用、已累计原生资金费和剩余退出费；当前小时资金费事件尚未计入，与既有共同合同一致。没有任意填充丢失的小时净值。

独立审计不导入候选或其执行 helper，从实际 ZIP 的订单、公开夹具 mark/资金费和预算记录重新核对每笔净现金、资金费、周预算/固定数量、真实配对时钟、全部小时持仓和主观察 NAV。最大 NAV 误差约 **1.67e-13 USDT**。500 初始两腿为 2.4/4.9，1000 为 4.9/9.8，离散金额与原生资金轨迹确实不是同比例曲线。

**数值核对通过不意味着经济账户完整。** 原生引擎在有持仓但资金费事件缺失时仍能返回成功和一条数值曲线；独立审核将 `missing_funding_exposed` 标为不准入、收益未知，不能把缺失费用当 0。空仓缺口只说明本次实际无持仓，不证明交易所没有该结算事件，也不将未知 rate 改为 0。

**44 项不同组件检查通过**：21 项新增回调/负例/资金费时钟审核检查与 23 项封存 v3 的原生钱包/数量/意图回归。研究脚本语义 Ruff 通过。原始初始化漏 `maintAmt`、原生 custom exit_tag 接口失配及最初 JSON 类型比较失败均保留；仅修正技术接口或显式序列化差异，没有改信号、阈值、费用、资金或历史窗口。

## EOS 规格来源的新证据和限制

作者[FFA Working Paper 4/2023](https://quantitative.cz/wp-content/uploads/2024/04/wp_2023_04_tadi_witzany.pdf)的封面、书目信息和 PDF 第 28 页（印刷页 26）Appendix Table VII 已实际读取/目视核对：EOS 最小交易量参考 .1 EOS、最大杠杆 75。它是早期预印本的表，不是 2025 发表版本的 Table 1；未给出最小名义金额或明确数量步长，也不能仅凭 2023 年表格认证 2025 年实际周的规格。

新固定 [Joh5Bot](https://github.com/JohnKearney1/Joh5Bot/tree/b3724b82627892659096b193011ec122a486f29c) 的完整 README。EOS 行给出 .1 EOS、.001 USDT 和 5 USDT 的参考。Git HEAD 真正提交于 **2021-05-06**，不能把网页显示的较晚归档年份当数据观察日期。文件 SHA 与 Git blob 精确匹配；只读 README/树名，未读 API/凭据文件或执行其交易代码。树中未见许可文件，没有采用其交易逻辑。

另读 [Binance 2024-09-17](https://www.binance.com/en/support/announcement/detail/a05ae5aad6e44f6c848bde0a4c94ad4b)及[2025-02-20](https://www.binance.com/kk-KZ/support/announcement/detail/f231705aaacd4c14932f6a788b94f588)的 EOS 保证金表：与所用小额头寸相关的首档上限 10000 USDT、MMR .5%、最高 75 倍有前后参考佐证。它们不是最小订单规则，不能单凭两份通告证明每个中间日期都没改过。直接下载均为 HTTP202 空响应；正文/表格由网页工具实际读取，不把空下载冒称完整本地原文。

[2021-05-12 通告](https://www.binance.com/kk-KZ/support/announcement/detail/556e80d3f5364f75aafaf1eb9e9f20c1)里的 EOS .111 USDT 是**限价单最低价格**，不是最小数量/名义金额；现货规格也不能转用至永续。

因此**EOS 在 2025-01-10 实际被提名周的数量步长/最小订单历史准入仍未解决**。不删 EOS、不换 A、不猜 5 USDT、不把合成规格当证据。`CopulaPortfolio.ft_bot_start` 继续直接拒绝来源 20 币资金启动；仅精确 FAKE 两币的夹具子类可运行。

## 社交来源与不同机制

Joh5Bot README 描述小时 Twitter 投票、多数方向、15% 余额配 10 倍杠杆、一小时后市价退出、无票/平票空仓。它是社会投票机制线索，没有本轮可验证的历史 tweet/vote/资金流账户输入，不能当作已验证情绪 alpha。与 Copula 的依赖结构不同，不等于已经通过实证去重或产生组合收益。

继续搜索 X/Twitter、YouTube、抖音，未新增完整可执行规则/字幕或可审计账户；父来源状态明确为复用，未观看视频、不采用宣传收益。新搜索出现的后续 adaptive copula/market overlay 论文仅是待审来源，没有读完方法、冻结实现或计算其收益，不能算本轮新策略或替换原始候选。

## 接下来的真实账户门槛

补齐 EOS 历史规格及完整原生真实资金 runner 的终止/缺事件准入后，才冻结原始三档阈值的 1000/500 真实钱包、15/8bps、同池趋势基准和 500+500 实际资金组合，运行 14592 小时共同窗口并逐笔/逐时审核。当前没有启动该真实矩阵，没有新的 Registry、factor_analysis 或 Vibe bench；回调/资金组件检查不能替代因子有效性、独立样本外、真实账户或实盘。

证据：[六场景审核](fixture_audit.json)、[源规格准入](metadata_admission.json)、[观察合同](observation_protocol.json)、[技术修订](technical_amendment.json)、[父封存复核](parent_revalidation.json)、[实现哈希](implementation.json)、[来源状态](source_intake.json)、[最终验证](verification.json)。实际 ZIP、订单、主观察净值与每次源码快照在对应 `user_data/crypto_quant/copula_fills_native_20261115_v4/runs/` 下保留。
