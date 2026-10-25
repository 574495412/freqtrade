# 中低频趋势／动量主线

2026-10-25 按用户最新指令收敛。现行优先级为 [research_tracks_v004.json](../research/crypto/research_tracks_v004.json)，旧轨道定义保留历史。当前研究不再同时扩展带单员复刻、资金费、NFI 加仓优化、港美股和高频家族。

本项目复用公开数据封存，持有两个固定趋势／动量候选的容量约束研究回放；TradingAgents 持有独立核验与决策；CZ-Agent 只读展示研究结果。**新增回放是显式研究模型，不是原生 Freqtrade 撮合的替代认证，也不连接交易所或启动模拟／真实交易。**

完整设计、48 个实际账户结果、风险／容量限制及下一步见 [TradingAgents 主线文档](/Users/chrise/GitHub/TradingAgents/docs/research/crypto_trend_mainline_20261025.md)。冻结训练选择现金，有效收益源尚未建立。

## 实验命令

```sh
# 新版本必须使用新目录。注册发生在数据准备与收益计算前。
.venv/bin/python -m research.crypto.trend_mainline register --run user_data/crypto_quant/trend_mainline_<fresh_version>
.venv/bin/python -m research.crypto.trend_mainline prepare --run user_data/crypto_quant/trend_mainline_<fresh_version>
.venv/bin/python -m research.crypto.trend_mainline run --run user_data/crypto_quant/trend_mainline_<fresh_version>
```

实际使用时替换占位名称。注册后源码／计划／输入摘要变化会拒绝运行，已有结果与失败尝试不会被覆盖。统一含独立审计及归因的入口在 `TradingAgents/scripts/trend_mainline.py`。

主实验目录为 `user_data/crypto_quant/trend_mainline_20261025_v001`，固定参数、原始输入身份与研究源码快照、每个情景的提交决策／部分成交／资金费／开放库存／完整生命周期／五分钟净值都保留。额外纯资本压力在 `trend_mainline_capacity_20261025_v001`，不改选原现金结果。

两候选只使用完成的小时／日信息；入口按过去成交额限量，随后区间真实成交额只影响模拟成交量。退出剩余数量保留、不会跳过亏损腿或静默清零。1 倍、固定 2ATR、0.25% 单仓计划风险、最多四仓且无加仓。完整定义以冻结 JSON 和实际源码快照为准。

历史币池选择、盘口冲击、交易所精度／最小金额和保护单执行仍未认证，成本为研究假设；已看过的历史不能重新命名为独立样本外。没有修改原生交易框架、已有冻结策略、旧模拟账本或实盘运行配置。
