# 期权原生权利金、费用与保证金验证：尚无更高历史净收益

本轮补完一个原生账户工程检查点：28项测试、10个最终隔离进程、26笔原生模拟成交、66个账户边界及2份实际原生start结果ZIP完成，独立精确分数审计通过726个币种金额标量。新增真实历史期权账户、接受的独立alpha、组合账户、factor_analysis及Vibe bench均为0。更高可比净收益的目标仍未完成。

原始完整比较窗口仍为2025-01-01 00UTC至2026-08-31 23UTC。本批合成边界是2026-11-15 21UTC至11-16 04UTC、到期之前；它没有替换原始历史窗口。BTC和ETH分币核算，不把1BTC与1ETH或不同产品账户拼成收益排名。

## 补齐的原生路径

Freqtrade固定上游2026.9提交 `c051e9065c5b677f03817dd1f83cb980af1ed684` 的595份原生文件未变。实际使用原生配置、ExchangeResolver、StrategyResolver/IStrategy、Backtesting构造、撮合判断、Trade/Order、原生订单关闭与策略回调。本地OptionPremiumWallets继承Wallets，按实际订单处理期权现金；本地fee/fill mixin替换不适用的永续资金费及强平假设。这不是上游官方期权账户支持。

两个完整start采用直接的原生构造，实际继承Backtesting.start，读取原生Feather历史、运行信号/撮合/钱包并导出ZIP。另八个进程是组件边界探针，不冒充完整start。FUTURES及合成linear-swap市场字段只是原生对象容器，不表示这些Deribit合约变成了线性永续，也不是实际Deribit交易接口。

合约身份和最小数量来自上一检查点的原始BTC/ETH元数据，字节SHA保持一致。价格、标记价、指数、市场深度和小时行情全部是显式合成输入；指数取相应行权价。fixture数量精度等于所选最小数量是局部假设，不是认证完整历史数量步长、交易带宽或成交可得性。

无期货、转账及跨币抵押时：现金等于初始币额加实际卖出权利金、减实际买入权利金与费用；净值另加有符号期权标记价值；S:SM保证金余额只取现金。各空头初始/维持保证金按当前产品文档公式单独计算后相加。多头浮盈不产生现金购买力；卖空收到权利金同时产生期权负债，不能直接当净值利润。[Deribit保证金定义](https://support.deribit.com/hc/en-us/articles/25944811317149-Margin-types-and-usage)与[当前产品公式](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options)原页已读取，另捕获原始HTML及SHA；当前文档不是2025年以来完整历史规则认证。

挂单使用明确的本地保守规则：空头入场预留毛初始保证金和手续费，不提前贷记未成交权利金；多头预留权利金及手续费。退出订单不提前释放持仓保证金，实际撤单才释放预留。没有把这个本地规则宣称为交易所风险引擎复现。

## 实际覆盖的财务边界

两币种各四项固定组件探针：

- 空头call/put、标记亏损、call分两次减仓、最终全部平仓。按实际成交数量记现金与费用，不重复加入generic Trade PnL。
- 多头付权利金、浮盈不增加可用资金、实际卖出后现金才可供下一笔买入；该组件期末保留put，未虚构卖出。
- 标记价格冲击后维持保证金不足，保留风险状态且可用下单额为0。没有生成假强平或将该路径报作完整历史账户。
- 未成交空头挂单不产生权利金现金，实际原生撤单后解除预留。

两个完整start每币种各4笔成交、2笔关闭Trade和16个before/after边界，期末真实为flat。原生原始报告开始于20UTC预热标签，第一资本边界是21UTC，终点为04UTC；没有把预热时段当成已入场账户。

手续费显式采用当前standard inverse-option 3bps underlying、权利金12.5%上限的模型，原生百分比/base fee和资金费均为0以避免重复扣费。交易所最新USDC日权maker优惠不适用于这里的inverse期权；未推断历史私人费率或VIP等级。[当前费用原页](https://support.deribit.com/hc/en-us/articles/25944746248989-Fees)。

独立审核不导入候选策略、钱包、费用或保证金实现，以Fraction从每个边界的实际原生订单、外部标记输入和合同重建现金流、费、持仓、期权价值、净值、初始/维持保证金、预留、可用资金及违约标志。726个金额标量精确相等，另核对每个边界的实际持仓与风险状态；不是从候选净值曲线反算同一净值。

## 原生ZIP不能直接用于净收益排名

ZIP含混合口径：Trade profit_abs及profit_total_abs是generic零百分比费用毛额，final_balance则读取本地钱包扣费后的币现金。实际订单已逐项与ZIP中的数量、价格、方向和时间绑定。

完整BTC合成start的毛交易差额0.004BTC，实际期权费0.00024BTC，现金增加0.00376BTC；ETH相应为0.04、0.0024及0.0376ETH。这些数字只是固定合成报价的算术证据，不是历史收益、作者业绩或可推广的收益率。日志里的20%平均单笔ROI、CAGR、Sharpe和其他generic指标均不作策略绩效采用，sidecar的strategy_net_return为null。

28项测试覆盖合同/标记时钟、当前保证金算式、原生数量精度、历史成交修改/删除/重复拒绝、费用/资金费重复拒绝、现金仅够权利金却不够手续费时在原生成交前拒绝，以及普通完整历史期权入口继续关闭。实际测试与最终进程分开，重复测试、调试重放及两个币种的合成缩放不计独立经济样本。

## 保留的失败与技术修正

最早失败目录和日志全部保留：合同timedelta序列化；浮点stake/price后原生数量截断成请求的一半；下一次退出会反转持仓时正确拒绝；原生配置忽略raw datadir而历史加载失败。没有据此改方向、数量目标、初始资金、费用、价格路径或时窗。

数量适配器只处理表示问题，要求经过原生amount/contract precision后的实际数量精确等于目标；否则拒绝。期权最小权利金名义额替换不适用的generic futures stoploss reserve最小额，真实现金/保证金/手续费仍独立预检。完整start采用显式datadir CLI覆盖及直接类构造，调试时的runtime class reassignment未进入最终证据。

原始protocol和单独full_start_protocol未改，最终32条实现/来源/拥有者路径在10个最终进程前冻结。四份新生产模块完整Ruff检查通过；研究脚本明确排除S101/E402。独立审计与后置验证未改变账户结果。595份原生文件、上轮期权72条、宏观分配398条及宏观输入43条封存文件均逐项复核。

## 尚未完成的目标与下一步

普通OptionBacktesting完整金融入口仍在原生构造前拒绝未支持账户。这里没有原始2025–2026完整历史期权链、历史可执行bid/ask/mark/index、存续与数量/仓位限制、历史费用风险规则、真实流动性/滑点、到期交割腿、期货对冲现金/PnL、强平过程和完整终点NAV。元数据当前获取不能回填历史first-seen。2026年8月起的期权转同到期期货再结算规则也尚未在本现金组件中执行。

交易所采用增量强平；维持保证金不足不能借用generic永续强平价格或假定一个合成平仓价来补齐账户。[Deribit强平规则](https://support.deribit.com/hc/en-us/articles/25944769313309-Liquidations)。后续应继续完成原生交割/对冲/风险生命周期及历史输入准入，再允许该机制参与完整账户比较。

因子选择仍须原生Registry及实际factor_analysis，金融组合仍须支持的信号接口及实际完整账户/适用bench链。本工程没有新因子或bench登记，未用“登记成功”代替实际回测。原宏观候选负结果、缺失资金费及其他历史UNKNOWN保留，不回调参数救失败。

本轮GitHub、X/Twitter、YouTube、抖音定向搜索未准入新的完整期权策略或收益证明；不声称新观看视频、阅读转录或复现社交账户。沿用父检查点的固定源码及原始读取范围，新增的是四份当前原始规则页核对，见source_intake.json及rule_source_receipts.json。

没有订单、部署、运行服务改动、提交或推送。整体高收益研究目标保持active，不能以工程验证完成替代更高可比净收益的证据。

证据：[原协议](protocol.json)、[完整start协议](full_start_protocol.json)、[技术修正](technical_amendment.json)、[最终进程](execution_receipt.json)、[独立审核](independent_audit.json)、[验证](verification.json)、[所有权](ownership.json)、[规则原始字节](rule_source_receipts.json)、[模块合同](../../../freqtrade/data/option_cash.md)。
