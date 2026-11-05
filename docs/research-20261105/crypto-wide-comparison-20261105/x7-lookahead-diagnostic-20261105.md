# X7 官方未来函数工具诊断

2026-11-05。固定版 Freqtrade `lookahead-analysis` 对既有 X7 候选执行的官方诊断：
2025 年第一季度、币安 USDT 隔离永续 ADA 和 ETH，工具实际检查 **10 个信号**，报告
`has_bias=False`、0 个入场标记、0 个退出标记、无指标标记。目标为 20 个信号，实际
未达到目标，但达到预设最少 5 个的运行门槛；工具对可用样本给出了结果。

这是有限范围内**未检出工具标记**，不是证明整份策略没有未来函数，也不是未来盈利、
最大回撤或实盘资格。X7 与 X8 实际触发的信号不同；X8 对应工具输出 12 个信号、3 个
退出标记，X7 的未检出不能解释或排除 X8 告警。两者各自保留已有策略专属配置；官方
工具会使用合成大钱包、market 订单、修改仓位槽等设置，输出不作为经济收益比较。

这次 X7 方案是在看到 X8 诊断标记和 X7/X8 历史收益之后登记，属于后补对照，不是
盲选、独立样本外或对所有市场环境的检验。未根据结果调整参数、策略或测试区间。

X7 使用此前合约账户同一 `v17.5.175` 源文件，SHA-256
`43daba51f067942f69c242fa6559fd591bb8adf1fa766e3c625f1c3ef1af0732`，与先前上游
快照一致；本机另一份较新上游 checkout 的 X7 哈希不同，不混称同一版本。运行前后校验
固定引擎 595 个文件和 21 个行情文件，未注入账户凭据、禁用 Python 字节码写入；峰值
RSS 约 2.82 GB、运行约 42 秒，退出码 0。RSS 限额 5.5 GiB、耗时限额 900 秒。

证据：[登记方案](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/lookahead-diagnostic-plan-x7.json)、
[官方输出 CSV](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x7_lookahead_check_20261105_v001/lookahead-result.csv)、
[运行及哈希回执](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x7_lookahead_check_20261105_v001/process.json)、
[原始日志](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x7_lookahead_check_20261105_v001/lookahead.log)。
方法参见 [Freqtrade 官方说明](https://www.freqtrade.io/en/stable/lookahead-analysis/)。
