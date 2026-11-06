# 冻结窗口内的公共历史深度抽查

按运行前登记取得 Binance USD-M 三份日档案。每份 34,560 行，发布方 checksum 核验通过。每份档案的双侧 ±0.2% 档位有 2,880 个相同的双侧采样时刻。各侧档位内时间有序且无重复，每条的深度与名义金额组合均不同。

| 窗口 | 合约 | 抽查日 | `bookDepth` | 同日 `bookTicker` |
|---|---|---|---|---|
| L | BTCUSDT | 2026-08-31 | 200，双侧 ±0.2% | 404 |
| S | BTCUSDT | 2026-10-23 | 200，双侧 ±0.2% | 404 |
| S | SOLUSDT | 2026-10-23 | 200，双侧 ±0.2% | 404 |

此前 2025-01-01 的档案只有 ±1% 至 ±5% 档位。本次结果确认冻结窗口内部分日期存在更窄的聚合档位。三个样本字段均为 `timestamp/percentage/depth/notional`。它们给出聚合深度，缺少同刻 bid/ask、逐价位数量、百分比参考价和历史接收时刻。三个 404 只对应登记中的路径。完整 RD1 仍需分别核对其 bid/ask 参考下的 20bps 深度、时间与规则。

请求代码和范围见 `audit_public_depth.py` 与 `registration.json`。`result.json` 保留 HTTP 回执、哈希和逐档质量结果，`verification.json` 核对 9 个原始文件和双侧采样时刻。原始压缩文件、校验文件及错误响应位于隔离的 `user_data/crypto_quant/rd_program_20261105_v1/public_depth_window_probe_v001`。历史准入仍为 false，金融运行新增 0。

Tardis 的 USD-M 文档提供增量盘口、快照、买卖报价和采集端时刻的来源说明。其文档还列出 2026-10-16 起的公开资金费历史采样频道。该频道的时间表示 REST 采样与记录，可用于验证观察边界，私人账户的成交和资金费结算仍需账户来源回执。全窗口数据需取得合法可读的副本，再按固定币池、预热窗、来源序列和时点规则验收。 [Tardis USD-M 来源文档](https://docs.tardis.dev/historical-data-details/binance-futures) [Binance 公共数据与校验说明](https://github.com/binance/binance-public-data)
