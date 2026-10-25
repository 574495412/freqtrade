# 核心因子、策略与外部来源

2026-10-25：统一研发入口为 [TradingAgents/factors](/Users/chrise/GitHub/TradingAgents/factors/README.md)。本文件以下为历史映射；新组合读取共享因子分数，不在执行层复制公式。

2026-10-24最新：[完整盈利交易重复与大额利润保护](crypto-signal-lease-20261024.md)完成四规则×三成本的12个同账户验证。3bps重复版637个小额完整持仓、每天3.46笔、净胜率74.57%，但小额净亏33.66USDT，全部12个小额层均亏。大额保护提高主档胜率但降低该档联合收益，没有上线候选。现有62份账户档案按期间／成本／精度分8组，不等于62个独立策略。另收集39币234个1分钟档案，直接归并因零成交旧价延续被拦截；未改原始值，仍需明确无成交观测时段的执行规则。

此前[真实成交流与成交活跃度研究](crypto-activity-absorption-20261024.md)复用520个官方月档，登记18,175个因果吸收事件，以25／29／33特征做同样本月度树消融。18套模型、9项策略／成本筛选及4项配对预测增益检验已完成；没有合格预测，两个新增特征组均无确认增益。该机制淘汰，不新增资金账户。因子在[activity_absorption_features.py](../research/crypto/activity_absorption_features.py)，原始CSV的936个Decimal特征值、480个单位路径和108个预测独立核对通过。

此前[盘中净止盈与机会排序研究](crypto-intrabar-joint-accounts-20261024.md)完成9项小额单位比较及6个完整账户。现有50份可核验账户档案，按期间／成本／精度分6组，另隔离工程无效账户；不等于50个独立策略。美元净利润排序令相同大额组件的半年收益由2.00%升至5.21%、夏普代理0.67升至1.15、小时观察回撤2.62%升至3.03%。小额月度层实际17个持仓仍净亏，六个联合账户均未满足目标，没有上线候选。

原25特征入口为[interaction_features.py](../research/crypto/interaction_features.py)，退出机制在[intrabar_small.py](../research/crypto/strategies/intrabar_small.py)，机会排序在[ranked_first_hit.py](../research/crypto/strategies/ranked_first_hit.py)。排序利用已知预测与风险预算，不是新增价格因子；真实taker流此前也在[条件因子研究](crypto-conditional-factors-20261024.md)试过。考核使用币安ROI／PNL／夏普／回撤／完整仓位胜率口径代理，并补充索提诺、PF、期望值与频率；平台AUM、跟单PNL及排名仍未知。

此前35账户阶段：[成本感知回归研究](crypto-cost-aware-entry-20261024.md)用上半年12,782个单位拟合、下半年3账户验证：两个有交易账户仍亏，高概率门槛没有交易。[4个BTC残差账户](crypto-btc-residual-20261024.md)、[25账户多指标评分](crypto-performance-scorecard-20261024.md)与[3个恢复／风险止盈账户](crypto-inventory-recovery-20261024.md)均保留，未取得联合实盘资格。

此前[直接策略研发与11个完整合约账户比较](crypto-sweep-rotation-20261024.md)运行了区间回收、截面轮动、压缩突破／回踩及RSI2，未筛出合格实盘版本。下文盘口采集和模型实验为既有历史记录。

辅助支线为[真实盘口与成交流研究](crypto-microstructure-20261024.md)：六币前瞻数据已接通，新增12个接收后可见的微观特征。第一段主采集已结束并记录重连，第二段未启动，未计算经济标签；该支线不再阻塞策略研发。

2026-10-24此前条件因子组合研究：两批34个特征、6组组合、24套双目标模型已完成筛选，尚无组合晋级。公式、结果、原始数据与可复算入口见[条件因子研究](crypto-conditional-factors-20261024.md)。核心计算在 [conditional_factors.py](../research/crypto/conditional_factors.py) 和 [conditional_context.py](../research/crypto/conditional_context.py)。

前一版小额趋势回撤、大额趋势突破的OHLCV自主策略已经全年诊断淘汰，完整目标与失败见[自主策略研发](crypto-autonomous-objective-20261023.md)。下文公开成交量和旧候选保留历史记录。

2026-10-23更新。当前没有已通过全部晋级要求的独立盈利因子。NFI与日线通道的完整账户组合通过历史经济筛查，但属于回顾式组装，TradingAgents选择cash，实盘资格关闭。最新结果和模拟运行边界见[组合审阅](crypto-ensemble-review-20261023.md)。代码存在、作者回测、框架成熟度与本账户可执行收益分别记录。

用户最新明确以合约为主，参考 Omofun、Damorne。当前新增因子入口为 [prepare_perpetual_flow.py](../research/crypto/prepare_perpetual_flow.py)，策略和仓位机制在 [perpetual_flow.py](../research/crypto/strategies/perpetual_flow.py)：

| 因子／机制 | 计算或约束 |
| --- | --- |
| 20分钟相对动量 | 当前完成K线收盘／4根前收盘−1，减同刻40币中位数 |
| 成交量突变 | 当前5分钟quote volume／前6根均值，再作横截面排名 |
| 主动成交方向 | 2×真实taker buy quote／quote volume−1；零成交保持未知 |
| 流动性与方向筛选 | 过去12根quote volume和的排名；收益顶部／底部十分位与残差、主动成交同向 |
| 风险与有界追加 | 固定2ATR止损；实际净利润结算后最多追加一次，新增止损风险受利润约束 |

完整规则、适配差异和账户结果见[合约研发筛查](crypto-perpetual-screen-20261023.md)。这些是待验证的研究假设；下文保留早期候选与外部来源记录。

## 本轮实际运行的因子

统一计算入口：[strategies.py](../freqtrade/research/strategies.py)。它们是三个固定基准，目的是先建立成熟引擎中的可比结果，没有宣称新发现的alpha。

| 策略 | 输入与规则 | 当前状态 |
| --- | --- | --- |
| CryptoTrend | EMA24/96交叉，EMA200方向过滤，反向交叉退出 | 已完成三段及跨年连续回测，未通过经济验收 |
| CryptoBreakout | 前72小时高低通道突破，EMA200过滤，前24小时反向通道退出 | 已回测，未通过经济验收 |
| CryptoReversion | 20小时均值±2倍标准差、RSI14的30/70条件，回归均值退出 | 已回测，未通过经济验收 |

实际资金费已经计入交易损益，**目前没有把资金费作为这三个策略的入场因子**。成交量在此仅作有效成交过滤。TradingAgents复用了多空研究员审查训练结果，尚未实现自动发现与验证新因子；本次它与固定规则都选择了cash。

## TradingAgents已有的加密研究因子

以下代码仍由相邻仓库持有。其中funding.py已接入独立增量实验，训练筛查未通过；其余尚未接入首轮Freqtrade结果。详见[因子训练筛查](crypto-factor-screen-20261023.md)，不能拿基准指标替未接入因子证明有效。

| 机制 | 代码位置 | 公式或输入 | 接入需要 |
| --- | --- | --- | --- |
| 资金费carry及变化 | [funding.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/funding.py) | 最近24小时已知资金费之和的相反数，以及相邻24小时变化 | 保留原始事件和5分钟可见延迟；可复用当前资金费来源 |
| 价格对主动成交的响应 | [response.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/response.py) | 冲击残差反转、成交方向与路径一致性、跳过最近1小时的动量 | 连续分钟价格、quote volume和taker buy volume；当前小时表不足 |
| 持仓扩张×成交方向 | [open_interest_flow.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/open_interest_flow.py) | 正向OI对数变化×精确对齐的60分钟主动成交方向 | 带原始时钟的OI与连续分钟成交；不可事后补齐 |
| 大户仓位与账户偏差 | [position_tilt.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/position_tilt.py) | log(大户持仓多空比)−log(大户账户多空比) | 真实快照和可见时点；文件也含未来标签，标签不能进信号 |
| 资金费结算后退出压力 | [settlement.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/settlement.py) | 已结算普通事件的费率与固定时间衰减 | 区分普通事件、结算时间与可见时间 |
| 现货与永续对冲carry | [hedged_carry.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/hedged_carry.py) | 历史资金费特征与等基础数量双腿损益研究 | 双腿价格、库存、资金与执行；不是单腿Freqtrade策略 |
| 通用量价特征 | [alpha158.py](/Users/chrise/GitHub/TradingAgents/tradingagents/research/quant/factors/alpha158.py) | Alpha158的完成分钟适配 | 仅特征库，没有直接交易信号；不重新引入Qlib运行框架 |

## 外部发现与优先级

GitHub热度来自2026-10-22浏览页面的近似显示，仅用于筛选维护与复用线索。源码快照、确切commit与SHA保存于`user_data/crypto_quant/external_sources_20261022/snapshots_v3.json`，不运行外部安装脚本和自动更新器。四个来源均已取得固定版本；NFI已完成受限六币回测，另已按2023年底X4规则接入40币研究；官方Supertrend的2022固定版本已完成训练并淘汰。Hummingbot和Passivbot尚未回测。

| 来源 | 本轮固定commit |
| --- | --- |
| Freqtrade策略库 | `f3340ce11f5bdf62f598522e64d1f5638eaa13f5` |
| NFI（已取X7源码） | `1af55971d3a64703ec449078e09fdec257d0d3c6` |
| Hummingbot | `9af100d6822da7d2d0291a906c730ef172284ee2` |
| Passivbot（已取说明） | `772abadc10a5bbf258a84aa8209a9084699fa1bd` |

NFI首轮结果见[固定版本复现记录](crypto-nfi-validation-20261023.md)。原生未来数据检查通过，但两版均未通过分段交易样本要求。源码注释明确提及依据2026年损失案例筛选条件，所以这些历史段不能作为作者未见验证。已另行取得2023-12-31的X4默认版本，供下一轮按版本时间核验。

| 优先级 | 一手来源 | 可以加速什么 | 与本项目的差异 |
| --- | --- | --- | --- |
| P0 | [NostalgiaForInfinity，约3.5k star](https://github.com/iterativv/NostalgiaForInfinity)及[官方文档](https://iterativv.github.io/NostalgiaForInfinity/) | 直接复现现成Freqtrade策略；研究多周期入场、退出及仓位管理的贡献 | 官方要求5m，建议40–80个交易对、6–12仓；当前6币1h协议不适配。必须登记独立5m协议，不能改名后冒称原版复现 |
| P0 | [Freqtrade官方策略库，约5.6k star](https://github.com/freqtrade/freqtrade-strategies) | 复用可检查的小型策略作为对照，先筛Supertrend与TrendRider | 示例不等于收益认证。TrendRider源码标明参数在2026-03-23寻优，本项目2026段不可宣称对此版本未见 |
| P0研究机制 | [Hummingbot资金费套利源码](https://github.com/hummingbot/hummingbot/blob/master/scripts/v2_funding_rate_arb.py)与[官方YouTube讲解](https://www.youtube.com/watch?v=Ly8R5g3juxw) | 借用费率周期归一、双边方向、净费用和费率反转退出的明确机制；先复用已有funding因子 | 源码为跨交易所双腿策略，不能直接塞进单交易所Freqtrade冒称同一套利；示例默认杠杆不自动继承 |
| P1 | [Passivbot，约2.1k star](https://github.com/enarjord/passivbot) | 研究EMA价格带、回撤追踪与库存退出，作为独立机制对照 | 当前默认含逆向加仓的trailing_martingale；本轮禁止摊平，不能无声放开。完整策略另立资金与风险协议 |
| 待核实 | [HangukQuant的X主页](https://x.com/HangukQuant)、[作者关于资金费研究的公开文章](https://hanguk-quant.medium.com/quant-ai-singularity-fear-and-making-a-million-dollars-9b98e4bfe388) | 继续追踪作者原始讨论、可复现规则及代码线索 | X直接访问403，未读到原帖；作者文章可读。收益叙述不转抄为本项目验证结果 |

Hummingbot的YouTube页面可取得标题、作者、章节与描述，本轮机制判断以相应官方源码为依据，未声称观看完整视频。源码还存在[资金费率单位异常的公开问题](https://github.com/hummingbot/hummingbot/issues/8413)，复用机制时须重新核对费率单位和结算周期。

## 下一轮的有限任务

执行更新：NFI的40币全部账户已完成，因2026段完整交易不足而未晋级；资金费过滤、周换仓和官方Supertrend均已淘汰。20/10日线通道的低风险版在2025亏损，单策略同样未晋级。以下保留原研究顺序与边界，最终结果见训练筛查记录；当前固定组合仅作为模拟对照。

1. 先冻结外部版本和规则；每条候选登记数据、信号、退出、仓位、成本及淘汰条件。
2. 优先两条独立工作：NFI原版兼容性与5m数据准备；现有资金费因子的单因子及固定基准增量实验。资金费套利的完整双腿执行另列，不混用单腿指标。
3. OI、主动成交因子先检查数据覆盖；覆盖成立再接入，不用缺失填零制造信号。
4. 同轮最多三个候选，每个输出净收益、胜率、利润因子、含浮亏回撤及三档成本。模型只审查固定训练结果与机制，保留拒绝结论。
5. 已读过的2025–2026历史继续标为回顾式开发资料；不能反复选赢家后重新命名为独立样本外。本轮三个失败基准不因新增来源而修改旧结果。

这一优先级按源码可复用程度、数据可得性和研发工作量排序，尚未按收益排序。

## 已接入的NFI2023核心规则位置

40币研究当前使用2023-12-31固定X4源码，完整文件在[user_data快照](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/external_sources_20261022/nfi_pre2024/NostalgiaForInfinityX4.py)，commit为`16395b32d3c36ba728b2b489e9e51e44d3a8ff8c`。它是多规则策略，尚未通过消融实验把收益归因给某个独立因子。

| 机制 | 实际内容 | 源码入口 |
| --- | --- | --- |
| 多周期环境 | 本币及BTC的5m/15m/1h/4h/1d完成K线、趋势和涨跌幅保护 | populate_indicators，第5478行；informative_pairs，第4776行 |
| 回撤与反弹入场 | 多组EMA偏离、布林下轨、RSI、CTI、Williams %R条件；按enter_tag区分模式 | populate_entry_trend，第12861行；条件1示例从12881行开始 |
| 资金与库存管理 | custom_stake_amount分配首仓；grinding/rebuy分支调整持仓，含加仓和部分退出 | 第4232、4269行 |
| 持仓退出 | 基于持仓模式、当前收益及最近指标的定制退出，另有本地目标利润状态 | custom_exit，第3972行；不只是RSI入场后固定止盈 |

本地适配器为[nfi2023_compact.py](/Users/chrise/GitHub/freqtrade/research/crypto/strategies/nfi2023_compact.py)：仅固定1倍杠杆，并在所有运行模式使用相同原生指标精度。其余入场、加减仓和退出沿用上述源码。40币数据、三档训练结果及后续验证状态见[训练筛查记录](crypto-factor-screen-20261023.md)。

官方Supertrend的入场来自三组ATR趋势方向一致，退出来自另三组方向及原生ROI/追踪/止损。固定2022源码的数组兼容适配器为[supertrend2022.py](/Users/chrise/GitHub/freqtrade/research/crypto/strategies/supertrend2022.py)，没有NFI式加仓。三档2024训练均亏损并超回撤门槛，已淘汰；完整参数、来源和失败结果见上述筛查记录。

日线趋势规则为[daily_donchian.py](/Users/chrise/GitHub/freqtrade/research/crypto/strategies/daily_donchian.py)：20/10或55/20日通道、Wilder ATR20、小时确认、双向、入场固定2ATR止损与按风险定仓。[daily_donchian_risk.py](/Users/chrise/GitHub/freqtrade/research/crypto/strategies/daily_donchian_risk.py)只在训练后对两条规则统一减半风险，未更改通道或退出；看过后续结果后没有再调参。资金费周换仓入口为[funding_rotation.py](/Users/chrise/GitHub/freqtrade/research/crypto/strategies/funding_rotation.py)，按已知24小时资金费的相反数排序，不使用未来结算值。


## 2026-10-24新增：退出生命周期与相对资金费

[NFI有界生命周期](../research/crypto/strategies/nfi_bounded_lifecycle.py)保留固定2023源码的入场因子，比较原减仓、改全平、小额净盈利才改全平。三组完整2024账户均净亏0.0394%，无实际负向仓位调整，不能据此估计退出干预收益；也未达到小额频繁净盈利目标。

[两永续相对资金费特征](../research/crypto/funding_differential_availability.py)采用已结算费率差EWMA、1小时研究可用滞后和已知8小时间隔，以alt/BTC相反等名义仓位预测24小时carry。[固定单位标签](../research/crypto/funding_differential_labels.py)中，21个标准成本机会实际平均carry低于成本且价格价差亏损，不晋级。标签不可相加为账户利润，也不产生账户Sharpe或完整持仓胜率。完整证据见[本轮研究](crypto-nfi-lifecycle-carry-20261024.md)。


## 2026-10-24价格/成交量交互

[25因子交互特征](../research/crypto/interaction_features.py)覆盖多周期收益、VWAP/标准化偏离、价格路径、方向成交量代理、BTC/跨币相对强弱、资金费及波动/效率/流动性。固定线性、固定树和按月滚动树按同一冻结规则比较。小额层没有可靠高净正标签机会；滚动大额标签均值为正，但18项统一校正不通过，尚无联合候选。见[完整研究记录](crypto-interaction-learning-20261024.md)。重叠单位标签不是账户收益或Sharpe。
