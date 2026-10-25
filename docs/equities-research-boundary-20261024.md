# 港美股研究与三个项目的边界

2026-10-24：用户将已有加密研究扩展到港美股。股票研究入口由相邻 TradingAgents 的 `tradingagents/research/equities` 持有，结果汇入 CZ-Agent 已有研究面板。Freqtrade 的策略、运行进程、合约数据和成本账户保持独立。

本项目的可复用部分是研究方法：固定候选、训练后冻结选择、分开的验证／留出期、全部成本情景、连续账户、前缀和未来扰动检查。股票每日 252 期的风险指标不混用加密的全年频率。股票的每手数量、休市、股息和本币现金，不能套用加密永续的杠杆、资金费与 CCXT 订单语义。

Freqtrade 官方定位及支持列表见 [官方文档](https://www.freqtrade.io/en/stable/)。本轮没有宣称 Freqtrade 原生支持长桥，也没有向其回测器注册虚拟股票交易所。

| 证据 | 相邻项目位置 |
| --- | --- |
| 研究设计与结果 | `../TradingAgents/docs/research/equities_hkus_20261024.md` |
| 固定协议 | `../TradingAgents/tradingagents/research/equities/plan.json` |
| 原始与规范化数据 | `../TradingAgents/data/research/equities_hkus_20261024_v006/` |
| 108 个账户及训练选择 | `../TradingAgents/results/research/equities_hkus_20261024_v001/` |
| 展示协议 | `../CZ-Agent/docs/equities_research_handoff.md` |

首轮股票候选沿用固定研究思路和旧股票 EMA 因子，不复制本项目的生产交易实现。训练筛查后美股、港股均选择 cash；后来时间段的诊断盈利不能推翻冻结选择。后续新股票机制使用新版本协议，仍由 TradingAgents 保存，避免本项目与股票研究互相影响。
