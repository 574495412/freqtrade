# 多行情、多策略研发执行交接

2026-10-26已新增独立版本 `regime_portfolio.py`：目标权重加减仓、同方向生命周期加权成本、事件系数小时变化、退出确认后分配风险额度、持久部分退出与JSON恢复。新仓成交还受实际扣费权益／总敞口限制；相同符号反向先平仓，下一既定调仓才反向进入。旧引擎／旧账户不修改。

TradingAgents `research_tools/regime_program/run.py` 已执行56个成本／容量账户，独立审计在同包 `account_audit.py`，报告见 [首轮账户结果](/Users/chrise/GitHub/TradingAgents/docs/research/regime_program/accounts_20261026/README.md)。全部独立财务重算通过；事件策略全年高成本账户经真实JSON边界恢复，与完整回放逐行一致。8项新执行检查涵盖恢复、资金费、事件加减仓、退出受阻和风险停机。当前无合格盈利策略，研究不等于模拟或实盘交易。

2026-10-25：持续任务目标与状态的唯一来源为 [TradingAgents研发协议](/Users/chrise/GitHub/TradingAgents/docs/research/regime_program/README.md) 及同目录 `targets.json`；沿用 `TradingAgents/factors/` 统一因子根目录。Freqtrade负责加密研究执行，CZ-Agent现有因子界面展示目标与结果。

基础验收目标为1万USDT连续至少365日，两档单边成本8／15bps均满足扣费CAGR≥20%、最大观察回撤≤15%、日净值夏普≥1；挑战CAGR≥30%，不提高风险预算。以上是未来研究门槛，未表示当前策略达标。

研究执行要求：固定gross0.8；真实资金费和双腿成交分别入账，切换持仓真实扣费，共享账户与容量；跨年、跨状态和策略切换保持库存、净值高点、停机和未成交退出，不拼接独立赢家曲线。容量检查1万／10万／100万，10万为首个净收益为正且回撤≤15%的容量目标；成交额1%代理不能冒充盘口成交证明。

先复用现有 `github_factor_portfolio.py`、`factor_portfolio_refined.py` 的封存证据与已知限制，再按新版本登记新实现。旧账户与计划不可覆盖。没有最低研发天数；真实前向可补证，不设额外固定等待。本交接没有启动回测、模拟交易、实盘或生产进程。
