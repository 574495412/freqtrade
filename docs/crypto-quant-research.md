# 加密货币量化研发与上线目标

2026-10-24最新：最近累计35个完整原生账户，含本轮1个零交易账户。[成本感知回归研究](crypto-cost-aware-entry-20261024.md)已用上半年12,782个单位拟合、下半年3账户验证：两个有交易账户仍亏，高概率门槛没有交易。此前[4个BTC残差账户](crypto-btc-residual-20261024.md)、[25账户多指标评分](crypto-performance-scorecard-20261024.md)与[3个恢复／风险止盈账户](crypto-inventory-recovery-20261024.md)保留，尚无联合实盘合格版本。

2026-10-24当前主线已转为[直接策略研发与11个完整合约账户比较](crypto-sweep-rotation-20261024.md)。区间回收、截面轮动、压缩突破／回踩及RSI2均已实际运行；暂未筛出合格实盘版本。下文盘口采集和模型实验为既有历史记录。

辅助支线见[盘口与成交流研究](crypto-microstructure-20261024.md)：新增12个真实微观特征，第一段主采集已结束并记录重连；第二段未启动、经济标签未计算。该支线不阻塞现有历史上的策略研究。

2026-10-22在`codex/crypto-quant-research`建立首版。目标是获得成熟引擎中可复算的策略与经济证据，再推进独立模拟运行和上线准备。测试数、模型调用数和工具迁移不作为策略成果。

**最新目标更正：自主研发同账户“小额频繁交易提高净胜率＋大额仓位贡献利润”的合约策略，争取带单榜表现。** 已实现的双层策略、联合目标、完整账户实验和榜单统计口径见[自主策略研发](crypto-autonomous-objective-20261023.md)。既有 Omofun／Damorne 公开机制仅是参考，不是当前必须复制的规则。

**2026-10-24因子扩展：** 34个特征、6组组合、24套双目标模型已完成两批筛选，尚无组合晋级。账户拥挤度与价格吸收出现待验证线索，小额层仍未达标；完整证据、源码及下一轮方向见[条件因子研究](crypto-conditional-factors-20261024.md)。

核心因子的公式、源码位置、接入状态及GitHub/X/YouTube来源见[因子与策略清单](crypto-factor-map.md)。首轮完整结果见[验收记录](crypto-quant-validation-20261022.md)。

**2026-10-23 最新用户方向：主线为永续合约，参考 Omofun、Damorne。** 当前多空因子、止盈／追加机制、40币真实主动成交量数据及结果见[合约研发筛查](crypto-perpetual-screen-20261023.md)。早期1倍实验同样使用合约；另行研究过的现货／合约 carry 支线已暂停。旧模拟组合只保留工程验证记录，当前没有获准实盘的版本。

2026-10-23本轮交付更新：全部已登记历史经济账户完成；单策略均有失败项，固定组合通过回顾式经济筛查但TradingAgents选择cash。独立模拟包已完成真实公开行情启动、同库重启、合成非空状态恢复和单实例保护验证，核验结束后正常停止。完整结果、来源、限定条件、操作命令及生产晋级边界见[最新组合审阅](crypto-ensemble-review-20261023.md)。当前没有实盘放行。

用户随后确认：频繁交易、较大本金稳健、较小本金较高收益均研究，并分别比较。下一版采用[三个研发档案](crypto-strategy-tracks-20261023.md)和[机器可读定义](../research/crypto/research_tracks_v002.json)，具体本金与收益／回撤目标尚未确定；旧协议和结论不追溯修改。

首版具体发布对象、配置和操作命令已收敛到[上线方案 V1](crypto-launch-v1-20261023.md)：NFI 1倍＋20日通道低风险版，仅作为固定规则模拟发布，实盘资格保持关闭。

## 职责与研究范围

Freqtrade持有策略、数据适配、原生回测和模拟运行；相邻TradingAgents通过独立Python进程复用原生多空研究员，审查训练期结果并从事前固定候选中选择。交易运行不安装TradingAgents，模型不读取凭证、不直接下单。当前TradingAgents工作区已有其他研究改动，本版通过显式路径调用，不覆盖其源码。

首轮采用六个事前确定的流动性主流USDT永续：BTC、ETH、BNB、SOL、XRP、DOGE。研究本金1000USDT，单笔150USDT，最多四仓、1倍杠杆、小时信号。它们是研究假设，实际账户规模和部署位置尚未指定。三个候选为固定趋势、通道突破和均值回归，不做历史参数寻优，不读取带单员标签。

冻结协议位于`research/crypto/plan.json`。2024年用于选择，2025年与2026年1至8月用于分隔验证。历史已被其他项目研究使用，因此明确属于回顾式时间隔离验证，不能宣称全项目未见样本外。TradingAgents只看训练期汇总，候选选择在验证计算前落盘；其选择与同训练数据上的固定数值选择对照。两者选中同一策略时不能声称模型有经济增益。

## 成本与容量的相称检查

小资金、主流币、小时策略的首要问题是信号与风险。保留每侧5bps手续费和真实资金费；追加每侧0/3/10bps费用代理，作为简洁敏感性检查。额外费率不是精确滑点撮合，不把代理结果称为真实可成交收益。必要时才深入订单簿。

容量只先检查实际入场金额与信号前一小时成交额的比例。比例超过0.01%时标记复核；该比例是项目筛查标准，不是行业定律。通过也不证明分钟内流动性、限价排队或双腿同步成交。默认不新增复杂容量账户。

## 验收与推进

1. 数据：原始档案SHA一致，完整时钟、资金费事件不缺失、不填零；市场规则使用当次公开快照并披露历史限制。
2. 工程：复用Freqtrade回测，策略前缀计算与完整计算一致；运行原生未来数据泄漏检查。
3. 经济：验证及最终历史段分别报告全部成本情景、净收益、胜率、利润因子、交易数和含浮亏观察回撤。初始目标为各档净收益正、PF至少1.1、至少30笔完整交易、最大观察回撤不超过15%；这些是首轮项目目标，不追溯修改旧实验标准。
4. 研究增益：TradingAgents与固定训练选择比较；保留其拒绝或无新选择，不反复调用直到选中历史赢家。
5. 运行：通过候选生成独立dry-run配置、数据库和可复算启动命令，检查重启与持仓状态。无强制最低研发天数。
6. 上线：先确定账户、资金、主机和部署策略，核对当前持仓、订单、代码版本与回滚，再进行可审阅的生产切换。研究默认不推送、不修改现有CZ服务、不触发真实订单。

失败候选保留结果。新的机制、时间周期或参数需要新协议，不能因本轮失败而在同一最终历史段反复选赢家。

## 已实现入口

在本仓库独立`.venv`使用Python3.12。以下路径为本机示例，原文、回测、模型响应和数据库均置于Git忽略的`user_data/crypto_quant`，新实验使用新目录。

```sh
.venv/bin/python -m freqtrade.research prepare \
  --source ../TradingAgents/data/research/dual_alpha_long_horizon_20261015/public_v1 \
  --dataset user_data/crypto_quant/data_v003

.venv/bin/python -m freqtrade.research train \
  --dataset user_data/crypto_quant/data_v003 --run user_data/crypto_quant/新的运行目录

.venv/bin/python -m freqtrade.research review \
  --dataset user_data/crypto_quant/data_v003 --run user_data/crypto_quant/新的运行目录 \
  --tradingagents-root ../TradingAgents \
  --tradingagents-python ../CZ-Agent/.venv-tradingagents-lab/bin/python

.venv/bin/python -m freqtrade.research evaluate \
  --dataset user_data/crypto_quant/data_v003 --run user_data/crypto_quant/新的运行目录

.venv/bin/python -m freqtrade.research bias \
  --dataset user_data/crypto_quant/data_v003 --run user_data/crypto_quant/新的运行目录

.venv/bin/python research/crypto/prepare_paper.py \
  --dataset user_data/crypto_quant/data_v003 --run user_data/crypto_quant/新的运行目录 \
  --output user_data/crypto_quant/新的模拟运行目录
```

原生回测终点是包含关系，适配器显式使用右开边界前一秒，避免训练读取下一段首根K线。除2025年与2026年单独账户外，另跑2025年起至2026年8月底的全程连续账户，跨年不重置现金、持仓或风险。模型不根据这些结果重新选策略。

小时mark月档发现缺日时，仅按缺失日期请求官方日档并校验官方SHA，不向前填造价格。资金费保留原始事件，原生小时格式向下归整的最大偏差为26毫秒；估值采用官方小时mark开盘，而非私有精确结算。通过研究不证明实际盘口成交或完整盘中清算路径。

模拟包默认要求经济与泄漏筛查通过。可用`--diagnostic-strategy`明确生成未晋级策略的工程诊断包，此时回执固定为`engineering_diagnostic`，只检查行情、状态与恢复，不能冒充策略已通过。启动命令保存在包内`package.json`，没有交易凭证，始终`dry_run=true`。
