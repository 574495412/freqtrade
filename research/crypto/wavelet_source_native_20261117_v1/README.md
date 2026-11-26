# 小波预测来源：旧版拒绝，新版仅完成时钟核验

**本轮没有新增原生金融账户、净收益或合格alpha。** 固定旧DWT_Predict的历史预测存在训练时间前瞻，拒绝将其送入收益比较；当前作者新版是另一套实现，默认实际转入滚动路径，控制流时钟探针通过，但真实特征/标准化器/模型及原生账户未完成。没有把替身输出、来源抓取或代码检查叫做原生回测。

## 来源与工作流

检索入口是 [rmallarapu-bc/nateemma](https://github.com/rmallarapu-bc/nateemma/tree/221ee52b9cd04e26089adc7ff955a74a1fa3e3fd)，GitHub元数据确认其fork自nateemma/strategies。旧分支固定 `221ee52b9cd04e26089adc7ff955a74a1fa3e3fd`，提交日期2024-03-01；不是当前作者主库。选择 `binanceus/DWT_Predict.py` 的预测控制流，未选择DWT_Predict2，也未检查或执行整仓策略。

另固定作者主库 [nateemma/strategies](https://github.com/nateemma/strategies/tree/9bb7dab5df53203e69f60336749551a40767af0b) 的 `9bb7dab5df53203e69f60336749551a40767af0b`，提交日期2026-10-27，读取TS_Wavelet_DWT→TS_Wavelet→TSPredict的实际方法归属及相关窗口代码。共11份选定来源字节核对Git blob与SHA，不代表完整阅读或验证全仓。旧fork没有独立LICENSE，当前主库GPLv3，分别保留；未将新许可自动追溯为旧fork的许可。

执行所有权仍是本地 `docs/official-factor-research-20261106.md` 的固定Freqtrade核心 `c051e9065c5b677f03817dd1f83cb980af1ed684` 与受支持策略接口。当前只在来源准入阶段，未导入或启动Backtesting、未注册原生资金计划，亦没有Vibe因子分析或bench。后续若准入，必须完成原生特征/策略集成、完整账户和独立订单现金/NAV审核，不能用本目录控制流探针替代。

## 旧版：原始跳窗训练/回写不符合历史决策时钟

旧策略5m，声明model_window128、预测跨度6柱；原始回测分支调用 `add_jumping_predictions`，缓冲974行、每次前进973行。作者代码也明确提示跳窗存在有限前瞻。没有将其改成滚动、改变窗口或重选参数来救活。

仅提取9个原始方法AST，函数体未改。wavelet、scaler、regressor是**记录行索引的替身**，不执行PyWavelets/XGBoost、不学习或生成真实预测。使用已有BTC/USDT现货5分钟文件的3000行连续观测（2025-01-01 00:00至01-11 09:55 UTC），只是诊断载体，不是完整账户窗口。

| 原始控制流观测 | 时钟含义 |
|---|---|
| 首次fit特征0…967，标签6…973；回写输出0…973 | 较早历史输出使用了其之后的训练标签。 |
| 3000行中2999行输出的模型训练到该行之后；扣128行预热后2871行 | 是预测行的时间依赖，不是交易数、收益或XGBoost预测误差。 |
| 增加末尾200行后，过去974行的最终模型归属改变 | 尾段回填依赖数据集结束位置，不能把整段历史输出当当时可得信号。 |
| `convert_dataframe`在分块前对3000行整体fit scaler | 训练期归一化统计的可用时间也未隔离；未声称树模型数值必定因此改变。 |
| `pred_array = close_data`共享数组，写入预测后再取尾段标签 | 替身下最终fit有888个标签成为预测水印，证明共享写入路径；不声称真实XGBoost恰有888个标签改变。 |
| 128名义窗口在首个get_coeffs实际传给wavelet前变成126点 | 原切片取127点、奇数长度再去掉首点；未按注释改成128。 |

独立 `verify_chronology.py` 不导入探针，按974/973/6的整数切片重建每行模型归属，并检查原始AST中的数组别名与回写赋值，结论一致。它证明原始方法不满足历史输入时钟，不证明小波方法普遍无效或策略亏损。旧JSON参数和声明被保留，但未进行hyperopt、模型拟合或金融回测。

## 当前作者新版：真实派发路径及窄范围正面证据

当前TS_Wavelet_DWT没有自行覆盖派发；TSPredict中 `use_rolling=False` 的分支会调用TS_Wavelet在文件末尾覆盖的 `add_jumping_predictions`，该方法**转入其优化滚动方法**。不能仅看父类同名函数判定当前源码仍使用旧跳窗。

正确MRO的源码探针与独立AST归属核对均通过。随后执行原始派发、coefficient table滑窗/放置、滚动训练与结果回写方法，采用CPU、无存储scaler分支；实际特征、wavelet、scaler、LightGBM及scale_array仍是明确的行时钟替身。6000行抽象索引得到：

| 预测行 | 特征训练截止 | 标签训练截止 |
|---|---:|---:|
| 1000…3499 | 993 | 999 |
| 3500…5999 | 3493 | 3499 |

1000行启动、2500行分块及6柱跨度均保留。全部5000个输出的模型标签在预测窗开始前已成熟，5000个结果缩放窗口也止于各输出行之前。截断到5800行，既有输出的模型训练时钟不变。独立整数参考重建匹配。

这只证明**所测CPU冷scaler路径的控制流时钟**。没有运行真实标准化特征、真实DWT、LightGBM/MLX，也未验证已存储全局scaler的训练来源。真实特征计算和模型数值的前缀一致性仍未知，更不是原生策略初始化、完整账户或收益认证。不能把旧版结论套给新版，也不能仅凭当前探针放行新版。

当前原生研究Python只有numpy/pandas/scipy等基础库，pywt/sklearn/xgboost/lightgbm/mlx/finta未安装；完整Framework/BaseStrategy归一化和依赖尚未固定。未安装新包、导入外部pickle/joblib模型、执行仓库脚本或启动服务。下一项工作是冻结实际特征/标准化/模型和隔离原生集成合同，再进行完整账户，而非从这些替身预测构造收益。

## 审计自身的纠正与失败保留

初次当前派发探针遗漏了TS_Wavelet末尾的覆盖方法，构造了错误MRO，并复现了不属于当前默认路径的 `predict_data` 参数错误。独立方法归属断言失败后，**该当前TypeError结论已撤回**。旧探针、输出、验证代码字节全部保留于 `invalid_dispatch_probe_v001/`，不会作为当前策略缺陷或晋级依据。更正记录见 `dispatch_probe_correction.json` 和 `upstream_verifier_failure_v001.json`。

滚动时钟探针首次还遗漏了调用前应存在的 `predicted_gain=0` 列；补齐了TSPredict原始populate_indicators的输入前提，未改作者函数，原失败日志保留。此错误与旧版训练时钟问题无关。

本轮所有有效辅助脚本Ruff通过；`verification.json`区分来源问题、审计器错误、正确的新版派发及尚未认证的完整流程。失败不会被改写为收益，当前主库的宣传绩效也未采用。

## 证据与边界

`protocol.json`在行时钟诊断前冻结；原始来源为 `source_receipts.json` 与 `upstream_followup_receipts.json`；旧版时钟为 `chronology_audit.json` / `independent_verification.json`；当前派发为 `upstream_dispatch_audit_v2.json`，当前CPU时钟为 `upstream_clock_audit.json` / `upstream_independent_verification_v2.json`。最终准入、核验、封存见 `admission.json`、`verification.json`、`artifact_manifest.json`。

本轮X/Twitter、YouTube、抖音查询没有取得可完整核验的新规则，未观看视频或读取字幕，不采用社交收益。搜索只用于发现，不把网页摘要作为模型或财务证据。

上一轮排列熵312路径清单保持不变，其4个有效账户仍未通过冻结财务门槛。本轮新增金融账户、收益比较、合格独立alpha均为0；加密持续目标未完成。没有订单、部署、服务、凭证变更、提交或推送，也没有与港美股、40币永续或其它合同合并排名。
