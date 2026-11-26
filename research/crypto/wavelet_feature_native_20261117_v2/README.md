# 小波 CPU 数值与 Freqtrade 原生特征接入

本轮沿用加密量化目标。结论为 **真实数值及原生特征接口通过，完整策略和资金验证待完成**。新增资金回测账户 0，新增合格 alpha 0，尚未证明收益更高。未下单、启动交易服务或部署。

## 来源、归属与版本

作者来源为 [nateemma/strategies，固定提交 9bb7dab](https://github.com/nateemma/strategies/tree/9bb7dab5df53203e69f60336749551a40767af0b)，保留 GPLv3。`TSPredict/TS_Wavelet_DWT.py`、`TS_Wavelet.py`、`TSPredict.py`、`utils/Wavelets.py` 等沿用上一轮已封存源码；本轮补取 Framework、归一化等依赖，各有 Git blob、SHA256 与固定 URL 收据。

`freqtrade/data/wavelet_cpu.py` 是依据作者数值规则新增的**本地扩展**，不是 Freqtrade 上游策略。实际接入检查使用官方 Freqtrade 提交 `c051e9065c5b677f03817dd1f83cb980af1ed684` 的 Backtesting 构造器、DataProvider 和 IStrategy 指标回调，官方 595 文件在运行前后校验。未调用资金回测 `Backtesting.start`，未生成交易结果 ZIP；不能把本次构造器和指标调用当作官方资金回测。

这次真实执行了所选作者类/方法的 AST 函数体及实际数值库，避免导入无关 TensorFlow/MLX/GAN 顶层副作用。不是作者整个框架启动或作者发布回测的复现。当前是明确选择的冷启动 CPU、无已存全局 scaler 分支，未加载外部模型；未验证 MLX 分支一致性。

## 冻结的数值契约

- 默认 15 分钟，六根 K 线向后收益率，按源代码二进制运算后保留四位；六根标签跨度不等于持仓或调仓周期。
- 实际滚动 128 根 median/IQR 标准化，`min_periods=1`，IQR 下限 `1e-6`，截断至 `[-10,10]`。小波输入是归一化的 `gain`。
- 保留实际类属性 `TSPredict.scale_results=False`，同时记录配置对象该字段为 True；没有根据配置描述擅改运行属性。
- 64 根窗口，`bior3.9`、symmetric、DWT level 2，形成 101 列系数，前 63 行零值。PyWavelets 提示该级别所有系数有边界效应，原样保留；这条警告本身不等于未来数据泄漏。
- 真正执行的 CPU 预测器是 `MultiOutputRegressor(LGBMRegressor(n_estimators=100, random_state=42))`，每次外层拟合包含 101 个系数模型。它不是名义 SGD 元数据指向的训练器。
- 初始训练 1000 行，预测块 2500 行；第一次 X 为 `[0:994]`、Y 为 `[6:1000]`，之后从第 3500 行重训练，X `[0:3494]`、Y `[6:3500]`。保留作者预测截断 `[-3,3]`。
- 输出是作者归一化空间的预测，未验证其为可直接解释的价格收益百分比。

真实依赖为 PyWavelets 1.8.0、LightGBM 4.6.0、scikit-learn 1.9.1。作者未锁定这些包版本，本轮版本是明确记录的研究参考。前两者安装在本研究独立目录，后者复用既有封存目录，未改基础环境；wheel/PyPI 元数据与安装文件均有哈希。

## 数据与实际检查

原有 BTC、ETH 现货 5 分钟数据先截断到 `2026-09-01T00:00Z` 之前，再严格聚合 15 分钟，要求每根恰有三根、无断档，不填充。各 67,200 行，覆盖 2024-10-01 至 2026-08-31。

本次模型诊断只用各币最前 6000 行（2024 年第四季度），与前 5800 行截断、最后 200 行 OHLC 同乘 2→10 的扰动输入比较。两币合计六个真实源码案例、12 次外层拟合。源收益、标准化收益、实际公布预测的前 5800 行及两个训练矩阵均严格相等；这 12 次拟合不是 12 个策略或账户。真实小波逆变换误差均小于 `1e-10`。

本地模块标准化值及完整系数表与作者实现逐项相等，输入缺失/无时区/断档等契约检查合计 **3 passed**。实际官方 DataProvider → IStrategy 指标接口对 BTC、ETH 各 2000 行的预测也逐项相等。另对本地模块完整 BTC 6000 行运行，覆盖第二个重训练窗口，仍与作者数值逐项相等。`known_at = 开盘时刻 + 15 分钟` 是模拟完成 K 线时钟，不是历史真实首见时间。

`verify_results.py` 不导入预测器或执行账户，独立重聚合原始数据、检查标签时间、模型参数/训练矩阵收据、输出哈希、截断/扰动结果及原生输出。结论见 `independent_verification.json`。

未来财务验证参考完整窗口仍为 **2025-01-01 00:00 至 2026-08-31 23:00 UTC**；本轮 6000 行数值诊断没有覆盖或替代这段资金窗口。

## 保留的失败与修正

1. 首次数值运行已实际拟合模型，但独立逆变换诊断把 Pandas 只读数组交给 PyWavelets，报 buffer read-only。`attempt1/` 保留原代码、日志、错误；只将该诊断输入改成可写副本，没有改源码数值或模型参数。
2. 下一次被原有 1 GiB 磁盘保护在计算前拦截，`attempt2_disk_guard/` 保留失败。随后仅将 21 个全 SHA256 相同、同设备同权限的历史不可变 K 线重复文件改为硬链接，保留全部路径和内容，共享约 140.73 MiB。文件系统 mtime 随源文件共享，未声称它是历史可见时刻。未降低磁盘阈值；`storage_dedup_receipt.json` 可核验。
3. 独立 Decimal 核算发现 BTC 第 4952 行恰为十进制半位：精确值 `-0.50625`，Decimal half-even 为 `-0.5062`，源 IEEE 运算后为 `-0.5062500000000012`，源四舍五入为 `-0.5063`。保留作者二进制值和所有预测。独立核算仅对此类精确半位且结果为相邻量化值时接受，未放宽一般容差。原核算脚本保存在 `verify_results_v001.py.txt`。

根目录旧失败/进度文件是保留历史，不代表工作仍运行。最终成功以 `numerical_admission.json`、`native_feature_admission.json`、`native_full_numerical_parity.json`、独立核验和最终封存为准。

上一轮旧 fork 的训练时间泄漏结论仍保留，不能把旧 fork 负面结果套用于本轮作者提交；上一轮已撤回的继承探针 TypeError 结论也不恢复。

## 尚缺的完整策略验证

本轮 `WaveletFeatureProbe` 的买卖接口明确拒绝执行，避免零信号假账户。作者完整入场条件、公共质量/可交易性门槛、退出/止损/仓位、参数文件覆盖顺序和执行契约仍须逐项恢复、冻结并接入原生接口。`BaseStrategy.check_precision_columns` 在固定源码第 704 行确实定义，AST 已核对；不将它误列为缺失方法，也未声称整个作者启动失败。

尚无预测效力、分组收益、独立样本外、完整账户净收益/回撤或多策略收益证据。数值一致性和选定截断检查不能替代这些证据。既有现货与 40 币永续结果继续按资金、标的、杠杆、成本及账户结构分组，本轮不更新全局收益排名。整体持续研究目标保持进行中。

## 复核入口

- `protocol.json`、`implementation_freeze.json`、`native_interface_freeze.json`：运行前范围与代码冻结。
- `source_receipts.json` 及三个补充收据：固定作者源文件。
- `numerical_outputs/`：六组真实模型输出和训练参数/矩阵哈希收据。
- `native_feature_admission.json`、`native_full_numerical_parity.json`：实际原生调用及第二训练窗一致性。
- `remaining_integration_contract.json`：完整策略未完成项；`admission.json`：当前证据等级。
- `verification.json`、`artifact_manifest.json`：本轮及前序证据完整性封存。
