# NFI X8 官方未来函数检查：结果与判定语义

2026-11-05。固定版本 Freqtrade 的 `lookahead-analysis` 对 NFI X8 输出 **`has_bias=True`**：
实际检查 12 个持仓样本，报告 0 个入场时间异常、3 个出场时间异常，`biased_indicators`
为空。**X8 没有通过本次官方检查，三个提示的原因尚未查明；不能将其认定为误报或已排除
未来信息。** 相反，这个测试也尚不能定位具体哪一行策略源码读取了未来数据。

检查范围仅为币安 USDT 永续 ADA、ETH 两对和 2025-01-01 至 2025-04-01；两币及窗口是在
既有历史结果已知后选择的。官方诊断使用大模拟钱包、市价单、调整后的持仓槽位和
10,000 USDT 固定投入进行重跑，不能作为实盘成交或经济表现。计划的目标是 20 个样本、
最低 5 个；只检查到 12 个，范围不足以代表全部币种、时间、策略分支或未来行情。
[冻结诊断方案](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_wide_20261105/lookahead-diagnostic-plan.json)、
[官方工具 CSV](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/lookahead-result.csv)和
[完整日志](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/lookahead.log)
均保留。CSV SHA-256：`0d89fa2f4c1f0fb352604b138d9cbb820fbf5a24da968e1034c2620835538f7a`。

三个提示在原始日志中的交易对和 UTC 时间是：

| 交易对 | 开仓时间 | 原账户平仓时间 | 日志位置 |
| --- | --- | --- | --- |
| ADA/USDT:USDT | 2025-01-19 22:50 | 2025-01-20 03:05 | [日志第 119 行](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/lookahead.log:119) |
| ADA/USDT:USDT | 2025-01-20 17:40 | 2025-01-20 21:50 | [日志第 144 行](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/lookahead.log:144) |
| ETH/USDT:USDT | 2025-01-27 16:40 | 2025-02-02 00:45 | [日志第 169 行](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001/lookahead.log:169) |

日志写“找到 13 笔”，但本固定版本源码计算展示用的 `found_signals` 是结果行数加一；
CSV 的 `total_signals=12` 是实际送入检查的数量。见[官方工具源码](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/optimize/analysis/lookahead.py:203)。

## 源码级含义

固定引擎提交为 `c051e9065c5b677f03817dd1f83cb980af1ed684`，其中
`lookahead.py` 的 SHA-256 为 `25b02b2d4f6e4317d8855b01976a3fef112e56280b7a33954634ed4dc51e2cca`。
完整回放与前缀回放从相同起点开始；对每个待检查结果，前缀回放只加载该结果的一个
交易对，终点分别截断到开仓时间及平仓时间之后一个 K 线。见[前缀构造](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/optimize/analysis/lookahead.py:139)。

对于出场，工具检查单币前缀结果表中**是否存在与原结果相同的 `close_date`**；不存在就
递增出场异常计数。它不在此检查 `exit_reason`，也不要求找到的那行拥有同一个
`open_date`。这直接说明 CSV 中的 3 是三个目标平仓时间在相应前缀重跑结果中未出现，
不等于三次已定位的出场条件使用未来数据。见[时间匹配实现](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/optimize/analysis/lookahead.py:54)和
[计数代码](/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade/freqtrade/optimize/analysis/lookahead.py:162)。
CSV 未列出差异指标，并不证明所有策略回调、其他样本及交易路径都不存在未来信息。

X8 的 `confirm_trade_entry` / grind 模式会查询其他已开仓和槽位；scalp 模式也检查空余
槽位，见[固定策略源码](/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_20261105/sources/nfi-x8-1x/NostalgiaForInfinityX8.py:5367)。
单币重跑与双币完整回放可能形成不同的账户状态或历史路径，**这是需要进一步对照的
混杂因素，而不是对这三次提示成因的结论**。本次 CSV 和日志未保存上述三次完整／前缀
逐笔持仓状态，因此无法仅凭这些材料判断混杂是否实际发生。

## 最小语义复核

以下合成表只验证固定引擎的时间匹配函数，不重放行情、不改变源码、不证明上述三笔的
原因。结果依次为 `true, true, false`：同一时间但退出原因不同仍匹配；另一笔交易在相同
时间退出仍匹配；原开仓对应交易在另一时间退出则不匹配。

```sh
/Users/chrise/GitHub/CZ-Agent/.venv-freqtrade/bin/python -B - <<'PY'
import json, sys
import pandas as pd
sys.path.insert(0, '/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
from freqtrade.optimize.analysis.lookahead import LookaheadAnalysis
t = pd.Timestamp('2025-01-20T03:05:00Z')
cases = {
    'same_open_and_time_different_exit_reason': [dict(open_date=pd.Timestamp('2025-01-19T22:50:00Z'), close_date=t, exit_reason='force_exit')],
    'different_open_same_exit_time': [dict(open_date=pd.Timestamp('2025-01-20T01:00:00Z'), close_date=t, exit_reason='custom_exit')],
    'same_open_different_exit_time': [dict(open_date=pd.Timestamp('2025-01-19T22:50:00Z'), close_date=t + pd.Timedelta(minutes=5), exit_reason='custom_exit')],
}
print(json.dumps({name: LookaheadAnalysis.report_signal({'results': pd.DataFrame(rows)}, 'close_date', t) for name, rows in cases.items()}, indent=2))
PY
```

下一步应保留完整双币与每个单币前缀的原生逐笔结果、开仓／平仓原因、仓位调整、
其他同时持仓及账户状态，再对上述三次逐条对照；还应扩大币对、行情和信号路径并检查
策略回调。验证未完成前，X8 实盘资格保持未通过。

## 补充：现货模式

同一固定 X8 源码在币安 ADA/USDT、ETH/USDT 现货和 2025Q1 的官方诊断也**未通过**：
`has_bias=True`，实际检查 11 个样本，0 个入场时间提示、3 个出场时间提示，未列差异
指标。三个 UTC 区间分别为 ADA 2025-01-19 22:50 至 01-20 06:45、ADA 01-20 17:40 至
21:50、ETH 02-28 02:35 至 03-01 06:30。日志显示的“12 trades”同样来自该固定版本
`len(results)+1` 的展示计数。其具体原因未查明，不能认定为误报。

该受限方案是在合约告警和现货历史收益已知后登记，使用无凭据的隔离诊断进程，官方
工具合成钱包和交易假设不构成账户业绩；样本不足以覆盖全部现货币对和行情。见
[现货诊断方案](/Users/chrise/GitHub/freqtrade/research/crypto/spot_baseline_20261105_v003/lookahead-diagnostic-plan-spot-x8.json)、
[结果 CSV](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_spot_lookahead_check_20261105_v001/lookahead-result.csv)、
[执行回执](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_spot_lookahead_check_20261105_v001/process.json)与
[逐笔日志](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/nfi_x8_spot_lookahead_check_20261105_v001/lookahead.log)。
