# 原40币NFI真实500资金账户：完成登记与有界存储预检

已登记两只独立500USDT原生钱包（每边15/8bp），完成真实官方构造器和源码指标的存储预检。**没有调用 Backtesting.start，没有新财务账户或收益结果。** 单币短前缀的存储通过不证明全40币完整账户资源足够。

共同合同继承[原40币计划](../strategy_comparison_wide_20261105/plan.json)：原有序40币、NFI X8 v18.0.146、Binance永续逐仓1x、6槽、99%可用资金、原生5m、源码限价/DCA/退出和原数据。账户窗口仍为2025-01-01 00:00至2026-08-31 23:00 UTC。500是真实待运行初始资金，不由旧1000曲线缩放。每个钱包有独立进程、user_data_dir和冷profit_max状态，不载入旧指标pickle。

本阶段保持**旧的条件执行模型**：原生整月精度启发式、存续币池、历史交易规则/资格、固定费用代理和实际结算限制均保留。它不是因果修正收益。AXS/TRB 2026-06-24 04UTC两个未知资金费时点须按新账户实际持仓重新检查；不能继承旧1000账户的空仓结论。见[主比较合同](../mainline_comparison_contract_20261117_v1/README.md)与[原40账户准入审核](../nfi40_account_admission_20261117_v1/README.md)。

## 实际预检结果

完整配置仍有40个币对，实际计算BTC/USDT:USDT的4000根5m（2024-12-29 05:20至2025-01-12 02:35 UTC），包含原800根预热；informative请求由真实NFI源码和官方DataProvider执行。

| 已观察项目 | 结果 |
| --- | ---: |
| 真实源码输出 | 4000行 × 231列 |
| DataFrame深层占用 | 7,531,828 bytes |
| 未压缩Pickle / Zstd | 6,981,002 / 1,816,087 bytes |
| 直接源码、压缩回调与强制磁盘重读 | 逐字段严格一致 |
| 解压Pickle SHA | `74ee21b7f8d726b3e2ac264b6cb8c57c471b4ad39361f6ada171a46f3be1d690` |
| 原生信号移位后行块 | 3199行，严格往返一致 |
| LazyTrim | 与官方trim结果严格一致 |
| 实测进程峰值RSS | 693,239,808 bytes |
| 计时区间 | 3.12秒，构造器/数据/源码/存储预检 |
| 区间最低可用磁盘 | 28,712,960,000 bytes |
| 财务start / 新交易 / 完成钱包 | 0 / 0 / 0 |

预算固定为5.5GiB RSS、最低1.5GiB空闲磁盘、4小时、最多1个财务worker，analyzed-provider LRU为4。数据完整窗口、币池或源码不因资源限制缩小。预检结束后12条冻结路径全部匹配；595份固定官方源文件、280份原数据和依赖契约通过runner校验。语法编译及Ruff通过（研究断言、继承单函数复杂度及长字符串使用S101/C901/E501豁免）。本次预检没有技术失败或财务重试。

## 原生所有者与本地扩展

- 官方引擎：`/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade`，commit `c051e9065c5b677f03817dd1f83cb980af1ed684`。
- 作者X8源码字节不变，SHA `fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3`。
- [native_runner.py](native_runner.py)来自原[run_wide_comparison.py](../strategy_comparison_wide_20261105/run_wide_comparison.py)；[derivation patch](runner_derivation.patch)记录本地存储、资源/并发守卫、start收据及时间单位比较修正。撮合、钱包、资金费、策略和观察计算保持原路径。
- [storage_adapter.py](storage_adapter.py)仅接入已存在的[Zstd DiskMapping](../nfi80_capacity_20261109/zstd_storage.py)和[v003 LazyTrimMapping / RowBlocks](../nfi80_historical_universe_20261109/v003/row_storage.py)，未复制或修改拥有模块。预检只释放该进程刚生成、已由原生provider及行块留存的中间压缩frame；旧封存数据和缓存未动。
- 透明文件系统压缩属于另一已完成的[资源预检](../nfi40_resource_preflight_20261117_v1/README.md)，不作为本阶段指标缓存重用或账户证据。

## 索引与调用边界

[protocol.json](protocol.json) · [plan.json](plan.json) · [implementation_freeze.json](implementation_freeze.json) · [preflight.json](preflight.json) · [preflight_execution.log](preflight_execution.log) · [preflight_completion.json](preflight_completion.json) · [preflight_artifact_manifest.json](preflight_artifact_manifest.json)

计划SHA：`7811964619044565a3927f2a4a03a43fdc8e04565b29d619d87593968650aaec`。预检入口为 `python -B -m research.crypto.nfi40_allocation_native_20261117_v1.preflight`，目录已存在时拒绝重跑。财务runner已准备但本阶段未启动；后续由主任务决定资源准入和实际完整资金运行，结果必须重新独立核对全部成交、资金费、费用、开放持仓和共同窗口净值。
