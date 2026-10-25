# 2026-10-24 合约策略研发：11个完整原生账户

已按用户“研究策略，不要无意义等待”的要求，将主线改为直接使用既有多年合约数据写规则、跑完整账户。此轮10个候选或风险对照、11个全年账户均已完成；最终没有候选通过其预登记的全部历史筛查，实盘资格保持关闭。

## 统一口径

40个当前可部署USDT永续品种，研究本金1000USDT，2倍杠杆。每侧5bps基础手续费加3bps成本代理，并计真实资金费。各账户独立计算，不拼接最好交易。收益为期间净收益；胜率按扣费后完整平仓净盈利计算；回撤列小时mark含浮亏观察与原生回撤中较大值，不能代表盘中最差回撤。

2024为本轮发现比较；只有压缩直接突破半风险版通过后进入2025。所有日期均已被项目此前研究使用，不能称独立样本外。40币当前可部署交集仍有存活偏差。

## 全部结果

| 候选 | 年份 | 净收益 | 净胜率 | 风险回撤 | PF | 完整交易 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| RangeReclaimOnly | 2024 | -24.44% | 49.08% | 24.53% | 0.587 | 3468 |
| RelativeMomentumOnly | 2024 | -21.75% | 29.58% | 37.01% | 0.860 | 595 |
| SweepRotation | 2024 | -41.50% | 45.90% | 49.26% | 0.775 | 3911 |
| CompressionBreakout | 2024 | 23.49% | 27.81% | 23.65% | 1.104 | 658 |
| CompressionRetest | 2024 | 42.41% | 27.22% | 30.72% | 1.203 | 540 |
| CompressionBreakoutHalfRisk | 2024 | 13.40% | 27.81% | 12.31% | 1.126 | 658 |
| CompressionRetestHalfRisk | 2024 | 22.76% | 27.22% | 17.10% | 1.240 | 540 |
| CompressionBreakoutHalfRisk | 2025 | -15.36% | 25.31% | 23.96% | 0.847 | 652 |
| RSI2DailyOnly | 2024 | -0.64% | 63.78% | 2.41% | 0.908 | 127 |
| RSI2DualHorizon | 2024 | -13.58% | 56.52% | 14.33% | 0.659 | 2916 |
| RSI2HourlyOnly | 2024 | -13.25% | 56.22% | 13.36% | 0.613 | 2853 |

## 规则与判断

区间回收小额层：15分钟K线穿过此前32根高低区间再收回，方向实体、至少一半影线、相对前24根放量至少1.5倍，加BTC已知24小时涨跌幅保护。大额截面层：7日收益除以7日小时波动率，40币稳定排名前三／后三，配本币均线与BTC20日均线方向，每日轮换。两层分别和同账户组合均失败。小额层每日约9.48笔，但费用前含资金费仍亏80.90USDT，减少成本不能直接建立正优势。

压缩突破：此前48小时区间宽度落在之前480个宽度的25%分位以内，放量且顺本币240小时均线突破；对照直接入场，处理组等待其后1至6根完成小时K线回踩、收回原突破位。原始风险版2024分别盈利23.49%与42.41%，但小时含浮亏回撤23.65%与30.72%，均失败。随后对两版统一把风险0.5%减为0.25%、保证金上限20%减为10%，信号和退出保持原样，只进行这一次风险调整。

半风险直接突破2024净赚13.40%、回撤12.31%、PF1.126，故按事前规则进入2025。2025净亏15.36%、回撤23.96%、PF0.847，停止其2026与其他成本晋级工作。回踩半风险版2024小时含浮亏回撤17.10%，不能用较好看的原生已平回撤13.65%代替它；仍不通过，没有再缩风险到刚好过线。低于28%的净胜率也未满足用户高胜率方向。

RSI2双周期：长期日线200均线方向内，RSI2低于5／高于95时逆短期回撤入场，恢复5周期均线或时间上限退出；小时小额2仓、日线大额3仓，原生同币只保留一个仓位，不加仓、不部分止盈、不限定只平盈利仓。小时、日线、组合三个账户全亏；日线版虽然净胜率63.78%，仍净亏0.64%，不能当盈利因子。小时版每天约7.80笔且费用前也亏48.66USDT。

此轮没有因失败而更换2024收益筛查门槛。原30笔、PF1.1、回撤15%是本轮沿用的研究筛查标准，不是用户承诺的收益要求。此前80%胜率、50%年化也只是早先研究假设。

## 可复核产物

- 汇总与每个账户结果、逐笔现金流审计、原生版本身份：`user_data/crypto_quant/strategy_focus_results_20261024_v001.json`。11个账户现金流对账均通过，最大误差约5.0e-9USDT；未新增原生引擎改动。
- 区间／截面规则：[sweep_rotation.py](../research/crypto/strategies/sweep_rotation.py)，[协议](../research/crypto/sweep_rotation_plan_v001.json)，[结果](../user_data/crypto_quant/sweep_rotation_run_v001/discovery_summary.json)。
- 压缩／回踩规则：[compression_retest.py](../research/crypto/strategies/compression_retest.py)，[协议](../research/crypto/compression_retest_plan_v001.json)，[结果](../user_data/crypto_quant/compression_retest_run_v001/discovery_summary.json)。
- 统一半风险：[compression_half_risk.py](../research/crypto/strategies/compression_half_risk.py)，[协议](../research/crypto/compression_half_risk_plan_v001.json)，[跨年结果](../user_data/crypto_quant/compression_half_risk_run_v001/discovery_summary.json)。
- RSI2：[rsi2_dual_horizon.py](../research/crypto/strategies/rsi2_dual_horizon.py)，[实际经济实验协议v001](../research/crypto/rsi2_dual_horizon_plan_v001.json)，[结果](../user_data/crypto_quant/rsi2_dual_horizon_run_v001/discovery_summary.json)。实验完成后仅移除无用import并整理导入；非import AST完全相同，原始源码在每个账户strategies目录及研究根快照保留，最新可运行清理版协议为[v002](../research/crypto/rsi2_dual_horizon_plan_v002.json)，没有把旧结果重新标为v002回测。
- 截面面板真实截断输入与完整历史前缀176640行完全相同；压缩信号9个真实截断检查通过；日线RSI特征9个截断合并检查通过，日线收盘只在实际完成后可见。这些是因果与记账检查，不是盈利证据。

## 外部资料与边界

截面动量的研究动机来自[Liu、Tsyvinski、Wu的论文](https://www.nber.org/papers/w25882)，只作为待检验的方向，不将论文收益套用到本合约账户。RSI动态均线退出参考[Connors本人2009年文章](https://tradingmarkets.com/recent/dynamic_exits_how_to_properly_exit_a_trade-641067)，原文讨论股票／ETF，本轮多空合约、小时周期、止损和仓位均为明确适配，不能称原版复现。

盘口主采集第一段已自然结束，267888条原始消息；深度有2个连接epoch、成交有3个。重连事实完整保留，第二段未启动，也未计算其收益标签。该支线暂作辅助，不再阻塞已有历史上的策略研究。

## 后续工作

完整同账户的高净胜率与高净收益目标仍未达到。下一轮优先把已经下载的真实主动成交量、延迟可见OI与拥挤度线索落到可执行的完整账户，对照同一价格触发条件是否有增益；先解释每笔收益来源，不再把概率标签统计当成账户利润。所有旧失败保留；没有新实盘、推送、服务重启或上线承诺。
