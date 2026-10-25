# 加密量化研发与模拟运行审阅

2026-10-23，开发分支 `codex/crypto-quant-research`，基础 HEAD `c051e9065c5b677f03817dd1f83cb980af1ed684`。本地尚未提交或推送；没有服务器部署或真实下单。

当前可审阅交付是固定规则组合的**本地模拟运行方案**。历史组合通过经济筛查，TradingAgents 仍选择空仓；尚无通过全部证据要求的实盘候选。下面保留失败结果和模型异议，不能把工程可启动等同于已发现稳定盈利策略。

## 核心机制和复用来源

| 机制 | 固定来源与本地实现 | 本轮结论 |
| --- | --- | --- |
| 多周期回撤、反弹与库存管理 | [NFI X4 2023-12-31](https://github.com/iterativv/NostalgiaForInfinity/blob/16395b32d3c36ba728b2b489e9e51e44d3a8ff8c/NostalgiaForInfinityX4.py)；[1倍适配器](../research/crypto/strategies/nfi2023_compact.py) | 40币版本盈利；2026年1—8月只有12笔完整交易，未达30笔门槛。包含加仓和部分退出，不能将100%历史胜率解释为低尾部风险 |
| 日线通道趋势、ATR风险仓位 | [20/10、55/20通道实现](../research/crypto/strategies/daily_donchian.py)及[训练期统一减半风险版本](../research/crypto/strategies/daily_donchian_risk.py) | 20/10低风险版训练通过，2025亏损而淘汰；55/20训练已失败。不是完整Turtle复现 |
| 三组ATR趋势方向 | [官方2022 Supertrend](https://github.com/freqtrade/freqtrade-strategies/blob/bcca7d7aef0df373c29b80532c8c02663f25ae3f/user_data/strategies/Supertrend.py)；[兼容适配器](../research/crypto/strategies/supertrend2022.py) | 2024额外3bps净收益−19.46%，观察回撤50.42%，训练淘汰 |
| 资金费横截面周换仓 | TradingAgents原有Decimal资金费公式；[固定周换仓规则](../research/crypto/strategies/funding_rotation.py) | 同档净收益−18.88%，观察回撤32.73%，训练淘汰。不同币种双向持仓仍有价格风险 |

完整因子入口、GitHub版本、X访问限制和YouTube核实范围见[因子地图](crypto-factor-map.md)；所有试验和淘汰证据见[筛查记录](crypto-factor-screen-20261023.md)。X原帖没有成功读取，YouTube只核对了官方页面元数据与章节，机制判断依照相应源码，没有声称看过完整视频。没有依据社交平台收益截图授予策略资格。

## 固定规则组合的历史经济结果

组合将 NFI 与 Donchian20HalfRisk 两个**完整原生账户**相加：各1000USDT初始资金、各最多六仓，总初始2000、最多12仓。没有资金互转、定期再平衡、逐笔择优、改变交易规模或删除亏损交易；后续权重随各账户净值变化。不能与单个1000USDT、六仓账户直接宣称同资金同容量优胜。

协议 [ensemble_plan.json](../research/crypto/ensemble_plan.json) 冻结24份原生完整账户身份。**设计组合前已经见过两条策略的后续历史**；日期上的validation/holdout只是原账户阶段名称，组合没有独立样本外证据。连续账户与其他时间窗重叠，不能累计为独立验证次数。

基础每侧5bps费用，另完整重跑每侧0/3/10bps压力档，并计入实际历史资金费。下表列额外3bps档；为期间总回报，未年化。

| 时段，UTC右端不含 | 净收益 | 完整交易 | 胜率 | PF | 含浮亏小时观察回撤 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2024全年 | 16.19% | 182 | 58.24% | 3.47 | 5.84% |
| 2025全年 | 10.18% | 172 | 58.14% | 2.81 | 5.62% |
| 2026-01-01至09-01 | 5.14% | 91 | 47.25% | 1.98 | 2.61% |
| 2025-01-01至2026-09-01连续账户 | 14.46% | 265 | 53.58% | 2.25 | 5.62% |

共12个组合时段／费用档均满足原经济筛查：正净收益、PF至少1.1、至少30笔完整交易、观察风险回撤不超过15%。这只适用于组合算术，不撤销各分策略的失败。

[evaluation.json](../user_data/crypto_quant/ensemble_run_v002/evaluation.json)保留全部费用档；[portfolio_audit.json](../user_data/crypto_quant/ensemble_run_v002/portfolio_audit.json)独立读取原生ZIP内的实际成交、订单费及已记录资金费，核对全部账户终值、同步小时净值和完整交易，12项通过，最大成交现金流误差约1.71e-8USDT。审计没有再造第二个撮合引擎，也没有独立重算每个资金费事件。

小时观察回撤包含未实现损益，但不代表小时内最坏风险。三档固定费用是执行压力代理，不能替代盘口冲击、成交概率、实盘延迟或交易所故障验证。40币名单使用2023年底成交额排序和历史完整性要求，仍与当前可交易名单取交集，存在幸存者偏差；不是2023年真实可选全集的无偏回放。

## TradingAgents的异议与检查范围

[selection.json](../user_data/crypto_quant/ensemble_run_v002/selection.json)记录：`agent=cash`，`control=NFI_Donchian_EqualInitialCapital`。实际调用原生多空研究员与结构化选择共三次，没有因拒绝而反复请求。模型只收到2024训练数值，但同时明确获知设计者已经观察两条分策略的未来历史；`future_results_included=false`不等于设计过程独立。

模型关注回顾式组装、名单偏差及NFI加仓风险，没有为下一阶段或实盘背书。因此保留本组合为固定规则纸面对照，`agent_endorsed=false`、`production_eligible=false`。

两条组件分别通过原生20信号lookahead检查及20例前缀值对照。NFI前缀20例包含7个pure rebuy分支；普通lookahead强制无限仓位会禁用该分支，不能独自覆盖它。原生批量回测与逐周期分析中的空余仓位条件仍有模式边界；前缀检查验证指标和信号值，不证明所有加减仓生命周期与实盘完全相同。

## 模拟包与已取得的运行证据

包：[ensemble_paper_control_v001](../user_data/crypto_quant/ensemble_paper_control_v001/package.json)。两组件使用独立用户目录、SQLite、缓存、策略快照和1000USDT模拟钱包；不连接现有CZ策略状态。固定币池、杠杆和风险规则沿用对应账户。

启动器检查策略、原生Python源码、配置和关键依赖版本；拒绝`dry_run=false`、交易凭证及`FREQTRADE__`环境覆盖。组合启动前核对两套配置、独立数据库和总资金；任一子进程退出时停止另一套。单实例文件锁传给两条原生exec子进程，主进程意外死亡后，仍存活的子进程保持数据库运行所有权。停止工具只对当前命令与本包路径匹配的进程发送信号，不按旧PID盲停。

已经取得的证据：

- 两套原生程序均以真实公开行情启动，出现RUNNING心跳与明确模拟交易提示。首轮正常停止后，同库、同配置重启检查通过。自然模拟账本在该次核验时均为零交易、零订单，不能称已验证非空实际仓位的重启连续性。
- [独立恢复夹具](../user_data/crypto_quant/ensemble_recovery_fixture_v001/processes.json)用四个分离的原生Python进程验证写入和读取：NFI两次入场、一次部分退出、利润目标缓存；通道策略入场ATR和绝对止损。两项恢复均通过。夹具使用合成订单，不运行broker循环，数据库与模拟包完全分开，不进入经济结果。
- 模拟包重复启动拒绝、子进程exec继承锁，以及主进程被杀后仍拒绝重复启动的测试通过。全部54项研发测试通过；26条警告为旧参考源码和现有pandas接口的弃用提示。Ruff及Git空白检查通过。

运行收据位于包内`initial_boot_validation.json`、`restart_validation.json`、`supervisor_v002_validation.json`和`sessions/`。三轮启动核验已完成，第三轮用安全停止工具正常停止；当前没有常驻模拟进程，也没有自然模拟成交。准确的最终运行／停止状态见[current_status.json](../user_data/crypto_quant/current_status.json)，不以文档中的旧PID作为控制依据。

从仓库根目录启动本地模拟：

```bash
/Users/chrise/GitHub/freqtrade/.venv/bin/python /Users/chrise/GitHub/freqtrade/user_data/crypto_quant/ensemble_paper_control_v001/run_portfolio_paper.py
```

同目录安全停止：

```bash
/Users/chrise/GitHub/freqtrade/.venv/bin/python /Users/chrise/GitHub/freqtrade/user_data/crypto_quant/ensemble_paper_control_v001/stop_portfolio_paper.py
```

## 推进边界与下一步决策

研发流程、外部机制接入、经济筛查及可审阅模拟包已经落地。实盘晋级仍未通过；不能因为组合历史为正或程序启动成功自动放行。

下一步以冻结规则收集原生模拟成交和决策证据，核对实际信号、最小量、加减仓及退出的运行差异。任何新策略修改都需新版本、新事前协议，并保留这些失败记录；已用过的历史不再重新标为独立样本外。不设置事后降低的交易笔数门槛，也不承诺固定天数后必然合格。

若要迁往服务器模拟环境，审阅对象是上述固定规则对照包；还需要明确目标主机、独立目录与资源范围，核对部署版本和回滚路径后才能操作。若讨论实盘，另需明确交易所、独立账户与资金风险范围并重新评估策略资格；当前文件不是实盘放行单。不要在聊天中发送密钥。
