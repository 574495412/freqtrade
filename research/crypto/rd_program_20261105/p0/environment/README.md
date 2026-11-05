# P0 独立研究环境与数据准入

本目录只做隔离研究准备。未读取任何 `.env`、私有运行数据库或账户凭据；未改 `.venv-paper`、官方核心、部署合同或模拟服务，未运行金融回测。

## 实际核验

- 本工程360个官方 `freqtrade/` 包文件逐一符合 `c051e9065c5b677f03817dd1f83cb980af1ed684` 的部署来源清单。根目录是带研究扩展的工作树，不能沿用旧runner“目录里出现任何额外Python文件即拒绝”的vendor树假设。
- 8个既有 `freqtrade/research` 扩展不被称作官方核心。新worker显式拒绝导入该命名空间及 `cz`，拒绝旧项目路径和 `FREQTRADE__` 覆盖，禁用字节码写入，拒绝网络连接。此为worker防错，不是操作系统沙箱。
- `.venv-paper` 的Python及旧研究9个数值依赖版本相同；额外运行依赖版本也记录在 `source_environment.json`。这是版本身份核对，不等于依赖所有平台行为相同。
- 对以下4个已登记数据集，实际流式核验全部920文件、1,246,097,328字节的SHA-256，并逐文件只读date列；评价窗口内规定时间网格无缺行、重复或乱序。没有重新下载、没有修改价格或重新证明历史首次可得时刻。OHLC/volume数值未在本轮重新审计。

| 数据集 | 文件数 | 原生datadir（相对仓库） | 收据 |
|---|---:|---|---|
| spot_L | 180 | `user_data/crypto_quant/spot_baseline_data_long_20261105_v001/candles` | 同级 `data-receipts.json` |
| spot_S | 180 | `user_data/crypto_quant/spot_baseline_data_short_20261105_v001/candles` | 同级 `data-receipts.json` |
| futures_L | 280 | `user_data/crypto_quant/nfi40_data_v002/candles` | `research/crypto/strategy_comparison_wide_20261105/data-receipts.json` |
| futures_S | 280 | `user_data/crypto_quant/nfi40_post_source_data_20261105_v002/candles` | 同级 `data-receipts.json` |

精确文件身份、旧23:00锚点、新23:55终点、配置/观察器/runner指针在 `data_manifest.json`。逐文件的日期范围、预热行数在 `data_file_inventory.json`。现货L/S最少分别有823/844根日线预热；合约L/S为1188/1848根。预热数据存在不等于指标信号已收敛。

现货具有5m成交价OPEN参考；合约**没有5m mark**，只有1h mark和5m合约成交价。旧简化订单缺创建时刻，不能靠数据hash通过补齐资金费同刻状态。X8退出告警未关闭。这些缺口不会被本目录改成通过，不能据本目录给经济晋级。

## 可执行检查与接口

从仓库根目录运行，只读检查不启动回测：

```sh
.venv-paper/bin/python -B research/crypto/rd_program_20261105/p0/environment/guard.py --dataset all
.venv-paper/bin/python -B research/crypto/rd_program_20261105/p0/environment/standalone_adapter.py --dataset spot_S
.venv-paper/bin/python -B research/crypto/rd_program_20261105/p0/environment/test_guard.py
```

`guard.py`提供 `verify_environment(snapshot)`、`verify_dataset(dataset, payload=True)`、`checked(record)` 和 `install_runtime_guards()`；`--metadata-only`不检查数据内容，不能标成payload已核验。

`standalone_adapter.load_prepared_plan(dataset_id)`把相对路径解析到当前Freqtrade工程，返回旧runner兼容字典；`load_legacy_runner(dataset_id)`只加载原runner及替换其来源/路径核验，不调用回测。它仅适用NFI旧小时锚点工程对照，保留原现金审计和lossless缓存；不把它当作P1新增终点的完整报告。无用的旧 `research_support` 不注入官方包，旧原件与hash不变。历史来源绝对路径只留在绑定的父协议原件，执行计划不再依赖它们。

原版B2/B3的P1执行入口见 `../../p1/native/README.md`。全部P1账户必须先生成独立注册manifest，再由主任务按预算串行执行；本目录不自动启动它们。新的RD1风险模块不施加在原版对照上。

## 验证记录

6项防错测试通过，覆盖私密/越界路径、篡改hash、四组独立路径、经济参数漂移、缺mark状态以及旧命名空间/网络拒绝；spot_S与futures_S只读preflight通过。首次测试发现旧候选的未使用 `original_source_files` 文档字段仍含旧绝对路径，已从新执行计划移除，父原件保持不变；此为首个金融运行前的技术修正。没有删除历史失败或重算经济结果。
