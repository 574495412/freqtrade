# 现货高收益外部候选：来源与可比性检查

2026-11-05。用户记得有收益更高的策略，这一线索确实存在。GeneTrader 的 +231.74%
是作者公开的现货历史报告，但并非当前 36 币现货账户的同口径结果。当前研究只比较
已登记的 X7 和 X8，尚未完成对 GeneTrader 等外部候选的共同条件经济回放，因此不能
把当前研究首选称为所有现货策略的最优。

## GeneTrader 作者报告

来源为 [imsatoshi/GeneTrader 固定提交的结果文件](https://github.com/imsatoshi/GeneTrader/blob/20053ba673a58976d5f5ab05cb2ec09018cbfd96/daily_results/20241223/gen10/results.txt)，
策略为 `GeneTrader_gen10_1734895087_6007`。报告披露 2024-11-18 00:00 至
2024-12-22 17:15 的现货回测，5 分钟策略、300 USDT 初始余额、最多 4 个持仓、113 笔
已列交易、期末 995.233 USDT 和总收益 +231.74%。报告本身没有给这些无时区日期标注
时区；不自行补称 UTC。总收益与 `(995.233 / 300 - 1) × 100` 的四舍五入口径一致。

报告的逐币表列出 70 个币对，其中 37 个有交易；与当前已冻结 36 币现货池相比，
前者有交易的币中只有 8 个相同。USUAL/USDT 一项披露利润 265.190 USDT，占披露总利润
695.233 USDT 的约 **38.14%**，该币不在当前池。当前现货账户使用 1,000 USDT、6 个
持仓、2025 年至 2026 年 8 月及 13/20bps 单边成本假设，不能直接将两个收益数字排名。

报告显示 113 笔全胜、0% 回撤，但也显示最低和最高余额均为 0、回撤时间为 1970 年。
这些报告字段不能证明全持仓在盘中没有浮亏；缺少原生成交、订单、持仓和逐时净值，
无法独立重算经济结果或实际风险。报告文本没有披露可核验的完整手续费与滑点合同。

[同一固定提交的优化记录](https://github.com/imsatoshi/GeneTrader/blob/20053ba673a58976d5f5ab05cb2ec09018cbfd96/daily_results/20241223/gen10/fitness_info.txt)
对同一策略记载第 10 代、5 分钟与 `2.3174` 的总收益值，并将其用于适应度评分。作者
这个数字属于与选择过程有关的历史披露，不能称为独立样本外结果；这里只确认该笔记录，
不推断所有其他代次使用了相同时间范围。

## 本地材料与复现缺口

- 已存[作者报告](/Users/chrise/GitHub/TradingAgents/data/research/github_genetrader_reports_20261028_v001/daily_results/20241223/gen10/results.txt)，
  SHA-256 `b99030f00bfb8b7f2491aef74947c1112ba5ffbd8b2737fd48406d37a43d88f2`；
  已存[匹配类名的策略源文件](/Users/chrise/GitHub/TradingAgents/data/research/github_genetrader_source_20261028_v001/GeneTrader_gen10_1734895087_6007.py)，
  SHA-256 `cf46b4b7fec71d508e69ce588c54bbc4af6856652c662d8ef2cc7ea9d7075819`。
  固定仓库的许可证文件为 MIT；复制时仍需保留其版权和许可文本。已找到源码不等于已
  验证它就是当次执行文件或已验证策略因果性。
- 固定提交中的同一结果目录包含上述源码、摘要报告和优化记录；仓库有示例配置，尚未
  找到该次运行的实际执行配置、逐笔原生导出或原始输入数据哈希。
  [Dockerfile](https://github.com/imsatoshi/GeneTrader/blob/20053ba673a58976d5f5ab05cb2ec09018cbfd96/Dockerfile)
  使用可变化的 `freqtradeorg/freqtrade:stable` 标签，无法确定当时精确的引擎镜像和依赖。
- 当前 36 币数据集有现货 5 分钟和 1 小时文件，源码静态声明使用这些周期和 BTC 5 分钟
  信息；但它不包含作者报告中的全部已交易币，且覆盖时间不同。当前环境中相关依赖
  模块可被定位，并不证明作者源码可在现行引擎运行或其功能一致。
- 因此，**原报告的精确合同目前不足以复现**。另行冻结当前统一账户合同后，可以将该
  源码作为新的独立候选进行兼容性与因果性核验、原生回放及逐笔审计；这类探索也不能
  冒充作者原版复现。当前 X7/X8 冻结方案和正在运行的账户没有为此修改。

## 因果性与引擎兼容性初查

在[固定策略源码](/Users/chrise/GitHub/TradingAgents/data/research/github_genetrader_source_20261028_v001/GeneTrader_gen10_1734895087_6007.py:375)中，
BTC 5 分钟数据先移位一行，再依照 pandas 行索引直接赋值给目标币行情，而不是按 K 线
时间对齐；所得值参与 `insta_signal` 的 BTC 入场子条件。一个[合成反例](/Users/chrise/GitHub/CZ-Agent/docs/crypto-spot-baseline-20261105/genetrader-causality-counterexample.json)
保持 02:00 及之前的输入完全不变，令 BTC 数据起点比目标币晚一小时，仅改变 02:55 的
BTC 值，就使 02:00 的 BTC 入场子条件由 false 变成 true。[复现脚本](/Users/chrise/GitHub/CZ-Agent/docs/crypto-spot-baseline-20261105/genetrader-causality-counterexample.py)
仅复现这几个数据处理表达式，未执行完整第三方策略或任何经济账户。这是**起点错位时的
条件性未来依赖**，没有证明其他入场条件同时成立，也没有证明作者历史报告实际发生此
错位。当前保存的 36 币现货 5 分钟文件具有相同的起点、终点和行数，但仍需验证实际
加载到策略与回调的数据切片；不能把本反例忽略，也不能仅凭它认定当前账户发生未来函数。

源码还把基于 5 分钟 `dataframe` 计算的 RSI 和资金流指标按索引赋给 1 小时信息表，
并在仓位调整回调中使用；它们不能未经核验就称为按 1 小时 K 线计算的指标。5 根居中
滚动后的 `shift(2)` 也不能仅凭 `center=True` 判定有未来函数，需按实际时间戳检验。
现有固定 Freqtrade 源码的现货模式兼容 `custom_sell` 旧回调，但这一静态接口检查不能
证明整份策略及其依赖可加载，更不能证明与作者原始引擎行为一致。源码保留原样，不
静默修复后冒充原版。

新增[同口径补充对照方案](/Users/chrise/GitHub/CZ-Agent/docs/crypto-spot-baseline-20261105/genetrader-post-results-comparison-plan.json)
仅供后续取证：登记时作者收益、短期现货及部分 X8 长期结果已经可用，明确属于结果已知
后的补充；先完成实际数据对齐、加载兼容和未来函数诊断，通过后才可在当前 36 币、独立
账户和既有两个成本档下回放。该方案不是作者报告的精确原版复现，没有启动账户，也不
改变 X7/X8 原有冻结方案。

## 其他未对齐的高收益线索

既有[公开候选登记](/Users/chrise/GitHub/TradingAgents/docs/research/regime_program/github_replication_priority_20261028/candidate_registry.json)
还收录第三方账表中 `NOTankAi_15_Cleaned_v2` 的极高披露值，口径为另一时间段、币池及
8 槽现货测量，尚未取得该次运行使用的精确策略源码、引擎、数据与原生账本，未本地复现。
此前针对**当前公开源码**的固定输入反例发现某入场分支依赖后续数据；因报告实际源码
身份未对齐，不能由此断言第三方当次测量使用的版本也存在相同问题。该值是取证线索，
不是可加入本轮收益排名的已验证账户结果。
