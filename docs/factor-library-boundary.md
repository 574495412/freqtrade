# 统一因子库与加密账户边界

唯一研发目录为相邻 [TradingAgents/factors](/Users/chrise/GitHub/TradingAgents/factors/README.md)。股票与加密共用公式及登记；本项目新的 `research/crypto/factor_portfolio.py` 只接收有时间戳的因子／预测分数，管理成本、资金费、容量、库存和退出，不复制上游因子。

完整协议、12个模型及64个代表性账户的结果：[本轮报告](/Users/chrise/GitHub/TradingAgents/docs/research/factor_lab_20261025/README.md)。现行轨道见 `research/crypto/research_tracks_v005.json`；旧轨道和冻结源码保留原身份。

撮合模型：小时分数／固定调仓时钟提交决策，过去成交额约束提交数量，随后五分钟真实成交额仅约束模拟部分成交；成交价为随后收盘加不利滑点。入口丢弃的非经济浮点预算dust不计生命周期，退出余量持续保留，风险退出不能被调仓弱化。期末不强平或重置，净值包含开放P&L及退出成本准备。

这些行为通过账户及独立重建测试，不等于原生引擎或真实交易所的撮合认证；精度、最小金额、盘口与保护单仍有缺口。现有原生策略、旧运行账本和生产开关不由该接口修改。
