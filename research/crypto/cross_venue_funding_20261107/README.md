# Binance 与 Hyperliquid 双腿资金费差

以 Hummingbot 固定源码 `9af100d6822da7d2d0291a906c730ef172284ee2` 为机制参考。复用每日费差归一、低费率端做多、高费率端做空，以及每日费差达到 10 bps 入场、逆转超过 10 bps 出场、双腿净收益超过 1% 止盈的默认规则。币池包括源码默认 WIF、FET 和对照 BTC、ETH、SOL。保留 2026 年 5–7 月与 8–10 月两个分别空仓开始的历史窗口。[固定源码](https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/scripts/v2_funding_rate_arb.py) · [登记](registration.json)

历史数据没有原脚本实时使用的预测费率和可成交报价。此处用延迟至少五分钟的上一笔已结算费率，按小时作决定，下一小时成交价开盘为价格代理，模拟成交时钟在开盘后五秒。双腿使用等基础币数量，最多持有七天，窗口末关闭未结仓位。Binance 按接口结算标记价计资金费，以 USDC/USDT 折算结算币。Hyperliquid 的结算基数是现货预言机价，公开资金费接口不提供该价，历史资产上下文档案请求未获数据。本次以结算前一小时的永续成交收盘价做代理，结果保留这一限制。每腿每次成交收 5 bps 手续费，另加 3 或 10 bps 滑点。净收益以入场两腿平均单腿名义额为分母。各币独立观察，结果是配对成交筛选，未构建共享资金、保证金和清算账户。[Binance 历史资金费](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History) · [Hyperliquid 资金费结算](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) · [历史资产档案](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data)

## 成本筛选结果

| 窗口 | 每腿单边成本 | 自然完成配对 | 平均资金费 | 平均扣费后双腿净收益 |
|---|---:|---:|---:|---:|
| 5–7 月 | 8 bps | 27 | +7.67 bps | −20.86 bps |
| 5–7 月 | 15 bps | 27 | +7.67 bps | −48.65 bps |
| 8–10 月 | 8 bps | 23 | +12.36 bps | −14.80 bps |
| 8–10 月 | 15 bps | 23 | +12.36 bps | −43.51 bps |

两个成本档的入场与退出路径相同，不合并当作独立样本。另有前窗 1 笔、后窗 2 笔窗口末退出，账本保留，表中不计。四组均有足够入场日期，但所有有成交的币种组均净亏。固定原成交和退出时刻，把费用和滑点都降为零，前后窗平均分别为 +10.91 和 +18.02 bps。仅去掉滑点、保留每腿单边 5 bps 手续费时仍为 −8.94 和 −2.49 bps。这是固定成交成本归因，未因费用变化重新触发止盈。[原始筛选结果](result.json) · [独立现金与因果时钟核验](verification.json)

复算原始响应哈希、106 条账本记录的开平仓决策时钟与双腿现金，最大净收益误差低于 6×10⁻¹⁶。各币价格和 Hyperliquid 资金费覆盖 4,489 个整点，含预热期及终点。Binance WIF 以 4 小时结算为主，其余四币为 8 小时。原始响应及完整账本在隔离研究输出目录。

用 35 份 Binance 官方月档和发布方 SHA-256 交叉核验 3,365 条已发生的结算记录，核对了 API 费率和月档公布的结算周期。月档到 10 月底，11 月 1 日终点事件仅有 REST 凭证。两份源均没有 6 月 24 日 04:00 UTC 的 WIF 预期结算记录，无法由公开数据判断该时点的真实支付。改用月档周期复算不改变配对时点和已知现金。每个前窗成本档各隔离一笔跨缺口交易后，前窗均剩 26 笔，8/15 bps 档平均净收益仍为 −20.26/−48.11 bps。后窗结果不变。四组均未通过成本筛选。[结算周期与缺口核验](funding_calendar.json)

```sh
.venv-paper/bin/python -B -m research.crypto.cross_venue_funding_20261107.study register
.venv-paper/bin/python -B -m research.crypto.cross_venue_funding_20261107.study run
.venv-paper/bin/python -B -m research.crypto.cross_venue_funding_20261107.verify
.venv-paper/bin/python -B -m research.crypto.cross_venue_funding_20261107.funding_calendar
```
