# 合约策略按官方指标研究：已执行口径与当前对照

最新[分钟级盈亏结构研究](crypto-minute-payoff-20261024.md)已完成3个完整账户与独立指标复算，近期比较集共65份档案、按期间／成本／本金／精度／执行约定分11组。1R及盈利回撤退出改善了账户收益与夏普，但小额层仍全部净亏，没有联合上线候选。以下各轮旧结论及原比较口径保留。

2026-10-24。当前仍没有满足“小额频繁净盈利＋大额层主要利润贡献”联合目标的实盘版本。此前35份历史账户档案按相同时间段、成本分成5组比较；随后3个NFI退出对照及3个因子组合组件账户均已完成，近期累计41份。[最新组件账户与路径核验](crypto-core-account-paths-20261024.md)列出净收益、夏普、索提诺、含浮亏回撤和完整持仓胜率；[NFI与跨合约资金费差研究](crypto-nfi-lifecycle-carry-20261024.md)保留前轮结论。以下35份比较保留原样。

随后[完整持仓路径学习](crypto-first-hit-learning-20261024.md)已完成三模型、双层、三成本的18项筛选，尚无联合合格候选，没有新增资金账户。该轮还识别出原生价格精度按整月K线推断的限制：它可能使用月内后续小数位。新研究使用入场前已闭合K线的精度代理；既有41份账户的实际影响尚未量化，不因此补作无泄漏或排名声明。

最新[完整资金账户核验](crypto-first-hit-accounts-20261024.md)已完成三个v2账户，主比较集增至44份，按期间、成本及精度口径分6组。另有一个v1工程失败账户单独隔离。月度树大额组件本档净收益+2.00%、夏普代理0.667、小时观察回撤2.62%，小额层仍为零交易，尚无联合上线版本。

## 官方依据与项目考核

依据[币安合约带单官方指标](https://www.binance.com/en/support/faq/detail/54aa6d3b43bc4f6eb4a3a6e3aea40acd)与[Freqtrade官方回测说明](https://www.freqtrade.io/en/stable/backtesting/)，同时保留以下指标：

| 维度 | 核心检查 | 口径 |
| --- | --- | --- |
| 收益 | 净PNL、ROI、CAGR、月度收益 | 计入交易费、成本代理、真实历史资金费和未实现盈亏；币安ROI使用MaxBaseBalance |
| 风险调整收益 | 年化Sharpe，另列Sortino、Calmar | 每个指标记录样本、收益基数及公式，原生已平仓和含浮亏净值口径分别保留 |
| 风险 | 最大回撤、回撤持续及恢复时间、波动 | 小时mark观察值，保留原生回撤；不冒称连续盘中最大回撤 |
| 交易质量 | 完整持仓净胜率、PF、净期望、盈亏比、SQN | 部分平仓不算一次完整平仓；高胜率不能替代正的净期望 |
| 稳定性 | 7/30/60/90/180日、月度、盈利日、频率 | 相同窗口与成本内比较；后续仍需成本压力与独立前向验证 |
| 账户结构 | 小额层与大额层笔数、利润、持仓时长和成本 | 单一完整账户核算，不能拼接不同实验的赢单 |

币安官方“Solid Growth”标签按Sharpe排名，但推荐榜还有高PNL、高ROI、跟单者PNL等不同类别。官方没有公布适用于所有策略的“Sharpe达到1或2就可以上线”的通用门槛。80%完整持仓胜率、50%年收益、15%回撤、至少30笔、PF1.1和小额层每日3笔，是既有项目研发目标或筛选假设，不是官方保证或排行榜资格。

本地币安式Sharpe代理采用每日增量mark净PNL/固定初始MaxBaseBalance的日均值/样本标准差×√365、无风险利率0；另报NAV日复合收益Sharpe。官方页面未完全公开每日端点与舍入细节，因此本地代理不等同于平台实际展示值。Sortino、Calmar也分别保留原生公式和补充研究公式。当前没有真实带单AUM、跟单者PNL、同期榜单分位或完整Smart Filter资格证据。

后续只读核查了[官方公开带单页](https://www.binance.com/en/copy-trading)的3个详情链接，保留于`public_copytrading_observations_20261024_v001.json`。其中2个链接请求30D、实际页面却显示7日，另1个显示30日；页面注明数据每1–2小时刷新。已记录这些公开组合的展示Sharpe、ROI、回撤、完整仓位数、AUM及跟单者PNL，但它们不是本项目的真实账户指标，也不构成完整排行榜分布。页面未提供可复核的逐笔账本、净值序列及精确截止时钟，且部分带TradFi标签；不能与本项目2024年的纯加密合约回测直接排名。窗口与快照核对优先于追逐页面高收益数值。

冻结口径：[performance_metric_contract_v003.json](../research/crypto/performance_metric_contract_v003.json)。实际计算：[performance_scorecard.py](../research/crypto/performance_scorecard.py)。原始交易档案、原生指标和既有晋级判定均未改写。

## 35个已有账户的比较结果

比较程序：[compare_official_metrics.py](../research/crypto/compare_official_metrics.py)。结果：`user_data/crypto_quant/official_metric_comparison_20261024_v001.json`。共35个不同账户，分为2024全年3bps的29个、2025全年3bps的1个、2024全年0/10bps各1个，以及2024下半年3bps的3个。每一组只在自身期间与成本内比较收益、Sharpe及回撤，不制作事后加权冠军分。

以下为2024全年、额外3bps/边、原生0.05%/边费用及历史资金费下的典型差异：

| 候选 | 净收益 | Sharpe代理 | 小时mark回撤 | 完整持仓净胜率 | 判断 |
| --- | --- | --- | --- | --- | --- |
| CompressionRetest | +42.41% | 0.785 | 30.72% | 27.22% | 收益较高，但回撤及胜率不满足联合目标 |
| CompressionBreakoutHalfRisk | +13.40% | 0.564 | 12.31% | 27.81% | 此年通过原经济初筛；相同版本2025年亏15.36%、Sharpe−0.817 |
| InventoryCoreGrid3 | −1.46% | −0.482 | 4.47% | 74.84% | 较高胜率仍未覆盖盈亏结构与成本 |
| AbsorptionDual | +0.35% | 0.528 | 0.55% | 56.76% | 37笔，小额层亏损、大额层仅3笔，不满足双层和频率目标 |

35个账户包含成本及风险对照，并非35个独立策略或独立样本外实验。多指标比较中的非支配候选也可能亏损或样本不足；它不是上线名单。

## 此前3组退出方式对照

来源为[固定2023年版本NFI](https://github.com/iterativv/NostalgiaForInfinity/blob/16395b32d3c36ba728b2b489e9e51e44d3a8ff8c/NostalgiaForInfinityX4.py)，保留原入场因子。**本轮是做多USDT永续合约，源策略没有空头入场实现。**

三组共用40个合约、2024全年、初始1000USDT、2倍杠杆、费用和数据：

1. `NFIBoundedPartial`：保留原部分减仓，最多减掉实际拥有的仓位。
2. `NFIBoundedFullExit`：原策略每次提出减仓时直接全平，盈利与亏损提案一视同仁。
3. `NFIBoundedDualExit`：小额层仅在完整持仓当前净PNL为正时把减仓转全平，其余保留部分减仓；大额层保留部分减仓。

风险预算固定于初始1000×99%=990USDT：每个小额仓位计划止损及费用预算0.99USDT，大额4.95USDT；同时最多2个小额和2个大额仓位。最多一次追加且不超过首笔，不在任何减仓后继续追加；止损绝不放宽，小额最长6小时、大额72小时。计划预算不保证覆盖未来资金费或跳空损失。原源代码会拒绝普通止损，本实验明确允许风险退出。

每个仓位的全部成交、费用和资金费合并后才判断净胜负。完整账户必须两层均净盈利，才按预登记条件考虑其他成本档与2025年历史验证；独立前向证据仍是后续步骤。

- 注册方案：[nfi_lifecycle_plan_v002.json](../research/crypto/nfi_lifecycle_plan_v002.json)。v001仅因运行器说明文字lint修订被替代，未计算经济账户。
- 策略：[nfi_bounded_lifecycle.py](../research/crypto/strategies/nfi_bounded_lifecycle.py)。独立逐笔核验：[audit_nfi_lifecycle.py](../research/crypto/audit_nfi_lifecycle.py)。指标复算：[review_nfi_lifecycle.py](../research/crypto/review_nfi_lifecycle.py)。
- 实际历史前缀检查已通过6组，每组24行、201列指标与信号完全一致；它验证所检时段的数据因果性，不是盈利证据。
- 仓位与指标13项测试通过。完整账户目录：`user_data/crypto_quant/nfi_lifecycle_run_v002/`，运行器逐个归档并在完成后写检查点。
- 没有真实订单、提交、推送、服务器部署或对原生框架源码的修改。
