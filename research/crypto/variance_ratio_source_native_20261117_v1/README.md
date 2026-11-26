# Donchian Surfer：方差比门控来源核验

**结果：本来源暂不准入金融回测；新增原生账户0、收益结论0。** 这是一项来源与信号语义诊断，不是零交易原生账户，也不是方差比因子普遍无效的证据。继续原有加密量化目标，不纳入港美股比较组。

## 来源、版本与工作流归属

固定作者源码 [sohan-shingade/flintstrats b02ac45c455e6cca9523673355090ef6909fc03b](https://github.com/sohan-shingade/flintstrats/tree/b02ac45c455e6cca9523673355090ef6909fc03b)，选择 `donchian_surfer.py` 默认值，没有执行作者优化程序、账户模拟器或 localhost:8000 服务。四个来源文件保留Git blob与SHA256，未改作者代码。源码树没有独立LICENSE或SDK依赖锁；SDK自身的MIT声明不能代替策略仓库的许可证明。

作者使用旧版 `flint.strategy.base` / `flint.models` / `flint.indicators` 接口，没有锁定SDK。采用 [PyPI flint-trading 1.1.0](https://pypi.org/project/flint-trading/1.1.0/) 分发包作**公开可校验参考**，上传日期2026-04-07、早于策略提交；不能证明作者使用该版本。分发包SHA与PyPI一致，选读模块逐字匹配归档。当前SDK仓库已改写为新接口，未用新版本替代旧语义。1.5.4 PyPI查询404原样保留，没有安装SDK。

本项目的执行归属仍是 `docs/crypto-quant-research.md` 与 `docs/official-factor-research-20261106.md` 所述Freqtrade原生接口。官方核心固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`；本目录辅助诊断不属于上游引擎回测。没有新增Vibe因子注册、SignalEngine或bench结果。若以后准入，必须先明确本地 `IStrategy` 适配、执行时钟、风险/成本合同并完成原生账户及独立现金/NAV审计。

## 原始规则与发现

原始市场为SOL-PERP，BTC-PERP提供方向背景；小时周期，48柱入场通道、24柱反向通道、14柱简单均值ATR、3倍ATR跟踪、120柱最长持仓、6柱冷却。48柱方差比以5步重叠收益方差除以5倍单步方差，门槛1.15；它不是显著性检验，也不能据此认定币种Hurst指数。BTC条件实际为16/48简单均值比较，不能按注释替换成EMA。

参考SDK `backtest/engine.py:273` 先把当前柱加入history再调用策略；`indicators.py:281` 和 `:288` 的通道也包含当前柱。对有效OHLC：

- 多头 `close >= highest_high` 只能在收盘等于当前高点、且为48柱最高点时成立；空头对称。
- 多头反向通道 `close < lowest_low`、空头反向通道 `close > highest_high` 无法成立。
- ATR跟踪及最长持仓退出仍可能成立，不能把整个策略说成数学上不可能交易。

没有将通道移一柱、换阈值或改市场。SDK `volatility` 为简单收益样本标准差乘 `sqrt(8760)`，ATR为14个TR的简单均值，均保留。原始配置名义仓位上限可达净值1.05倍；亏损缩仓及冷却状态、最大持仓退出不更新亏损计数的行为也保留，未擅自裁剪为1倍。

参考SDK `get_candles` 返回截至当前时间的背景历史。其默认FillPipeline在回调后处理订单，使用柱末可用时间判断延迟，并应用冲击/部分成交；不能直接称为Freqtrade下一柱撮合。保证金引擎为可选，默认构造未启用。作者服务选择了何种完整配置仍未证实，没有把参考SDK的默认金融合同归给作者。

## 已有真实数据诊断

使用既有Binance SOL/BTC USDT永续小时数据，各27048行，2023-08-01起；文件SHA在诊断前后核对，时钟完整、无重复且OHLC有效。不下载新行情、不补值。观察窗固定UTC **2025-01-01 00:00至2026-08-31 23:00，共14592柱**；保留窗前历史，因此不是作者2025年起无预热窗口的完整复现。

| 独立逐柱条件 | 柱数 |
|---|---:|
| 收盘等于48柱通道高点 | 0 |
| 收盘等于48柱通道低点 | 1 |
| 方差比达到1.15 | 2948 |
| 任一原始通道入场条件同时满足方差比 | 0 |
| 加入BTC方向后的独立空仓入场探针 | 0 |
| 反向通道退出条件 | 0 |

`diagnose.py` 只执行未修改的来源AST函数/类定义，使用仅记录意图的上下文替身，每柱重建一个空仓实例。没有资金账本、撮合、费用、资金费、连续持仓或经济收益，不能把探针当成交。1000组合成有效OHLC历史验证通道不等式；另验证极值相等确实允许入场、ATR与最大持仓退出仍可触发。

`verify.py` 不导入策略或诊断脚本，用独立Pandas滚动计算复核全部计数，并逐字复核额外SDK模块与分发包。首次复核在计数已通过后遇到相对/绝对路径处理错误，已修复并保留 `verification_failure_v001.json`；未修改规则、数据或结果。两个辅助脚本Ruff通过，S101/S102仅为明确注明的断言与固定AST诊断豁免。

## 准入结论与边界

没有拿到作者SDK绑定；在可核验参考SDK下，公开突破描述与实际条件存在上述差异，本窗口原始入场探针为零。因此**保留来源，暂不移植或新增完整账户**。这不是金融门槛失败、不是完整原生零交易结果，也不否定以后用独立新协议研究方差比。新协议不能回填成作者复现，不能靠本次结果改阈值救活。

X/Twitter、YouTube、抖音查询保存在 `social_discovery.json`；本轮没有从X/YouTube锁定新的可执行版本，抖音索引给出泛因子/组合讨论但没有可复现的加密信号与账户合同。不宣称看过视频或验证过其中收益。

最近完成的8个HAR/Naive原生钱包、固定组合及失败门槛仍见相邻 `har_vol_source_native_20261117_v1/README.md`，不重复计算为本次新增。此来源没有证明更高收益、没有增加合格独立alpha；持续目标保持未完成。无订单、paper/live、服务或凭证变更。

复查命令（在Freqtrade仓库根目录执行）：

```sh
.venv-paper/bin/python -B research/crypto/variance_ratio_source_native_20261117_v1/diagnose.py
.venv-paper/bin/python -B research/crypto/variance_ratio_source_native_20261117_v1/verify.py
```
