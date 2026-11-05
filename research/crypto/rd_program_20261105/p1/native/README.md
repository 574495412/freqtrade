# P1 原生B2/B3隔离账户入口

此入口由冻结注册表限定为16个B2/B3账户；只支持历史BACKTEST，没有trade/dry-run/live启动路径。创建本代码时没有运行任何金融回测。结果最多是工程复现与描述性比较，不自动给经济合格标签。

## 固定行为

- B2复用官方策略库固定commit `f3340ce11f5bdf62f598522e64d1f5638eaa13f5` 的 `AverageStrategy.py`，SHA为 `63606866fdf1cb3d71dc39a951ed0784a31686690b1c4014c12d2481c8dbf094`。GPL v3 LICENSE一并保留；源码没有修改。此次取得的两文件均与先前sources.json收据吻合。
- B2解析为4h主周期、5m detail、startup=0、EMA8/21、ROI=0.5、stoploss=-0.2、long-only。资金unlimited、6仓、99%可用余额，分别1000USDT；合约继承官方策略默认1x，不加载NFI覆盖或外部参数文件。已分别调用官方StrategyResolver确认两市场的实际解析值及空外部参数字典；没有运行回测。上游过期导入提示保留，没有改作者源码“修复”它。
- B3原固定NFI源、原历史配置、1x及原DCA/退出行为不变；禁止附加RD1风险合同。api/通知显式关闭，任何非空凭据字段直接拒绝。
- 使用本Freqtrade仓库固定核心与`.venv-paper`，复用旧已锁定native runner和lossless storage，仅替换来源验证与只读观察器，不修改撮合、钱包、指标或订单规则。
- 新观察器在native `time_pair_generator`首次yield某个时间前观察状态。只有前后都没有任何trade对象（包括待成交入场）且已平仓现金没变的无事件间隙才补flat；非flat缺口保留missing。它同时产生完整性已标明的5m参考曲线和小时锚点，绝不把4h快照伪装成5m持仓路径。
- 首次遇到 `current_time >= end` 时，在该事件yield之前冻结窗口；之后的原生事件继续原样透传，但不再更新观察器状态。窗口外平仓或现金变化不能用于回填窗口内缺口。独立审阅发现的此项边界错误已在首次金融运行前修正，反例保留为回归测试。
- 合约仍没有5m mark，5m成交价OPEN必须称条件性参考；现货OPEN也不是真实bid或可成交价格。新观察可记录引擎当时状态，不替交易所结算或因果审查背书。

## 注册与执行

主任务须先分配注册表ID并串行运行。示例：

```sh
.venv-paper/bin/python -B research/crypto/rd_program_20261105/p1/native/run_native.py \
  --prepare P1_B2_AVERAGE_NATIVE_spot_S_base \
  --registration-dir user_data/crypto_quant/rd_p1_native_20261106/registrations/P1_B2_AVERAGE_NATIVE_spot_S_base \
  --output user_data/crypto_quant/rd_p1_native_20261106/accounts/P1_B2_AVERAGE_NATIVE_spot_S_base

.venv-paper/bin/python -B research/crypto/rd_program_20261105/p1/native/run_native.py \
  --execute user_data/crypto_quant/rd_p1_native_20261106/registrations/P1_B2_AVERAGE_NATIVE_spot_S_base/run_manifest.json
```

`--prepare`只核验来源/数据并写新配置、计划、manifest，不运行账户。manifest记录冻结计划、源/适配器、依赖、输入收据、风险配置、成本、窗口、终值、候选阻塞和确切命令。实际启动另存start.json；既有输出或注册目录拒绝覆盖。重试须新目录、父运行身份及技术原因，并由主任务累计预算；本入口不自动重试。

manifest显式记录注册配置hash、主/detail周期、startup、订单类型及stake_amount，并在执行时与注册配置重核。B3为5m主周期、无独立detail、固定Binance源默认startup=800；这些默认值只做登记，不添加配置覆盖。`resolved_strategy_config_hash`指注册配置，完整运行时解析配置另存effective_config.json并计入结果hash收据。

`--execute`执行前后检查核心、策略、输入和适配器hash，使用统一排他锁 `user_data/crypto_quant/rd_program_20261105_native_worker.lock`；同时只容许一个native/diagnostic金融worker。原资源上限为RSS5.5GiB、3600秒；另加剩余磁盘10GiB下限，仅作工程资源保护。磁盘不足保存failure；原worker的RSS/超时触发保留resource_guard.json，退出码73不得当作完成。

每个成功账户保留原生导出、原生强制终止结果、独立Decimal成交/费用/资金费现金对账、小时净值、5m参考与缺口收据、新旧终点结果及完整配置。原生终点force_exit与观察终值不混为自然退出；若小时或5m路径不足，记录阻塞/失败，不填造持仓路径。P0因果/mark阻塞不会因运行成功自动解除。

完整输入/源码复核和结果保存后，先在 `retained_artifact_hashes.json` 保留本次配置、源副本、原生导出、NAV、现金审计及结果文件的SHA256；不遍历可重建cache，不把持续进度文件作为稳定证据。随后只清理**本次新建输出目录内**可由固定输入重建的 `lossless_cache`，保存cache_cleanup.json及证据hash收据的hash。拒绝symlink、注册计划不符的目录；不清理旧数据、原策略文件、原生结果、NAV或审计。失败输出及缓存保留，供问题复核；主任务不得因为磁盘保护改币池、精度或经济参数。

## 验证范围

```sh
.venv-paper/bin/python -B research/crypto/rd_program_20261105/p1/native/test_observer.py
```

9项合成状态/时序及配置测试通过：yield序列不变、flat补点、非flat不插值、未成交trade对象不能视作flat、窗口外平仓不得回填窗口内持仓缺口、窗口外现金变化不得改写窗口内flat尾部、B2冻结参数与非法运行ID拒绝、证据hash排除cache且保留结果、显式manifest字段保留B3原默认值。另以官方解析器检查两个市场实际参数。它们不是行情账户回测、真实交易或策略获利证据。正式原生金融路径仍需由后续P1执行验证；不声称该路径已被本次单元检查覆盖。

## 2026-11-06 执行后证据

以上验证范围记录的是首次执行前的状态。现已完成16个B2/B3原生账户及2次事前登记的确定性复跑；所有5m参考日历完整、现金核算通过。两次复跑的全部交易／订单／资金费字段、每个5m观察值和现金审计逐项相等。7份身份匹配的旧NFI档案也逐项相等；合约短窗压力账户无匹配旧档案，不补造历史parity。

首次B2尝试在正式配置校验时因关闭的通知对象缺少必需字段失败，金融回测次数为0。修复仅补空的禁用通知/API字段，新目录登记1次技术重试；新增官方schema测试后10项配置／观察器测试通过。原失败和注册清单均保留。

`native_scorecard.json`、追加的P1执行台账和仓库 `docs/crypto-factor-checkpoint-20261106.md` 保存结果。`audit_positions.py`只读重建方向敞口、DCA、持仓年龄和终值贡献，5项边界测试通过；不改变原生交易。所有结果仍是工程／历史参考，经济和生产准入仍为false。
