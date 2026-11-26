# 固定作者小波策略的原生交易规则接入

本轮沿用加密量化目标，将上一轮已验证的数值特征进一步接入作者真实入场、退出、止损、仓位及成交量确认回调。**当前证据为策略接口准入，新增完整资金账户 0，尚未证明预测效力、合格 alpha 或更高收益。**

## 固定来源和执行边界

- 作者 [nateemma/strategies，9bb7dab5df53203e69f60336749551a40767af0b](https://github.com/nateemma/strategies/tree/9bb7dab5df53203e69f60336749551a40767af0b)，GPLv3。新增 `utils/ExitPolicy.py`、`custom_indicators.py` 的 Git blob 与 SHA256 收据；其余依赖沿用已封存源码。
- 官方 Freqtrade `c051e9065c5b677f03817dd1f83cb980af1ed684`，实际调用 Backtesting 构造器、DataProvider、原生参数加载、IStrategy 指标/信号/止损及相关回调。官方源文件未改，未调用财务 `Backtesting.start`。
- `SourceWaveletCPU` 是**本地导入桥**，选取真实源码的类/函数定义并使用真实 PyWavelets/TA-Lib/sklearn/LightGBM 等依赖。排除无关 TensorFlow/MLX/finta 等顶层导入和副作用，没有假指标、假模型或占位风控。仅将小波类 `use_mlx` 明确固定为 False，并延迟注解求值；逐个 AST 投影哈希可独立复建。不是作者原始全模块导入/环境启动成功的证明，也不是作者发布收益复现。
- 相对上一轮“买卖接口拒绝执行”的特征探针，本轮执行了作者真实的完整最小指标、`get_entry_conditions`、`get_exit_conditions`、公共过滤及当前生效风险分支。未声称覆盖所有未启用的 ATR/MLX/神经网络分支。

## 实际加载规则

固定树未发现匹配 `TS_Wavelet*.json`。遵循官方加载器：原生默认 → 类 buy/sell 字典 → 当前策略参数文件 → API；一般策略配置可覆盖 stoploss/ROI 等特殊属性。本次无参数文件/API 覆盖，并从诊断配置中剔除另一策略的止损、ROI 和加仓设置，避免误继承。

21 个实际超参数与源码字典/默认值独立核对。关键生效值如下：

| 规则 | 当前生效值 |
| --- | --- |
| timeframe / startup | 15m / 128 根 |
| 跨币聚合 | **False**，由小波子类覆盖父类 True |
| scale_results | 类属性 False，另一个配置字段 True，保持作者实际语义 |
| 买入目标 | 正收益的 40 根均值 + 1.9×标准差，下限 0.5 |
| 卖出目标 | 负收益的 40 根均值 − 1.8×标准差，上限 −0.5 |
| 入场触发 | predicted_gain > target_profit，且成交量、rvol 和公共质量门槛通过 |
| 当前有效主要入场门槛 | guard_metric < 0；rvol > −1，其他质量门槛按源码原值保留 |
| 卖出信号 | predicted_gain < target_loss，rvol > 2，guard_metric > 0 |
| native ROI / 静态止损 | 3% / −5% |
| 宽止损窗口 | 2.6 小时 / −17% |
| custom take profit / 最大持仓 | 2.2% 净利润阈值 / 21 天，保留其他退出优先级 |
| 加仓 / trailing_stop | False / False |
| 确认成交量 | quote volume ≥ max(1000, 10×订单 quote size) |

预测仍在作者归一化空间中，未将其解释成已验证的未来价格收益百分比。标签六根跨度不代表持仓周期。

## 已执行的检查

实际官方策略生命周期读取 BTC、ETH 各 2000 根真实 15m 数据（2024 年第四季度），分别运行完整 2000、截断 1800、最后 200 根 OHLC 扰动三组。合计 **6 组、11,600 行、6 次外层拟合**，每次真实训练 101 个系数模型。

两币完整预测与上一轮真实源码 6000 行参考的相同前缀严格相等；截断/扰动前 1800 行的**全部 92 列**逐项一致。独立脚本重新核算六根向后收益、40 根目标和所有当前生效买卖掩码，通过。信号出现次数不等于成交次数，重复诊断输入的次数也不等于独立机会或策略数。

原生 `ft_stoploss_adjust` 调用实际 `LocalTrade.adjust_stop_loss` 的独立临时对象：开仓价 100 时宽止损 83，2 小时后价格 110 仍为 83；2.6 小时收紧至 95，价格再至 120 仍为 95。离散财务回放只能在实际下一次回调时收紧，不能将该诊断解释为精确秒级成交。止损与强制退出不受一小时退出否决，半小时 ROI/普通退出被否决，一小时 ROI 允许。

另通过实际原生 DataProvider 缓存读取两币真实指标，执行仓位上限、成交量确认及 **96 组 custom_exit** 参数网格检查：先检查过热/止盈，再检查 12h、1d、21d 退出。最小指标中没有原始 `rsi` 列，因此相关 RSI 分支当前不生效；未伪造 RSI 以激活它。这里的临时 Trade、利润参数网格和确认结果均不是资金账户或历史成交。

首次探针错把作者显示名 `SGD` 当作 Python 类名 `sgd_forecaster` 比较，断言失败。`attempt1_probe/`、`attempt1_execution.log` 与原生目录失败收据全部保留；仅修正自有诊断的名称判断，源码数值、参数和交易规则未变。后续成功由 `attempt2/admission.json`、callback 收据及 `independent_verification.json` 支持。

## 完整资金验证仍待执行

下一阶段必须先登记同一完整窗口、资金、成本、执行和缓存契约，再执行官方账户与独立 fills/cash/NAV 核算。当前接口检查不能替代 2025-01-01 至 2026-08-31 的完整账户、独立样本外、收益/回撤或组合结果。不得因作者宽止损或模型计算慢而改掉这些规则以换取通过。

上一轮的真实数值一致性、旧 fork 泄漏负面结果和已撤回的错误继承探针结论保持各自边界。持续目标仍进行中；未下单、部署或改动运行中的交易服务。

复核入口：`protocol.json`、`interface_freeze.json`、`callback_freeze.json`、`parameter_file_inventory.json`、`definition_projection_receipts.json`、`independent_verification.json`、`admission.json`、`artifact_manifest.json`。实际原生案例位于 `user_data/crypto_quant/wavelet_policy_native_20261117_v3/`。
