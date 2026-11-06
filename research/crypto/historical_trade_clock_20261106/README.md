# 逐笔成交时钟与冻结 5 分钟 K 线验收

在已有盘口证据的时段预登记四组对照。长窗取 2026-08-31 00:00–01:00 UTC 的 BTCUSDT 现货与永续，短窗取 2026-11-01 同一小时的 BTCUSDT 与 SOLUSDT 永续。每组使用 Binance 逐笔日档、官方 SHA-256 校验文件，以及 CryptoHFTData 的小时 WebSocket 成交档案。复核保留交易 ID、价格、基础币数量、maker 标记、交易所成交时刻、事件时刻与采集器时刻。现货官方档保留微秒，匹配供应商毫秒字段时按同一毫秒桶对照。[Binance 公开日档说明](https://github.com/binance/binance-public-data/) · [现货采集字段](https://www.cryptohftdata.com/docs/wire-format-binance-spot) · [永续采集字段](https://www.cryptohftdata.com/docs/wire-format-binance-futures)

| 市场和窗口 | 官方有效逐笔 | 最终匹配接收记录 | 冻结 OHLCV 一致 | 截止时缺成交 |
|---|---:|---:|---:|---:|
| BTCUSDT 现货 L | 231,415 | 231,415 | 12/12 | 3 根 |
| BTCUSDT 永续 L | 209,255 | 209,255 | 12/12 | 7 根 |
| BTCUSDT 永续 S | 45,156 | 45,156 | 12/12 | 2 根 |
| SOLUSDT 永续 S | 33,328 | 33,328 | 12/12 | 1 根 |
| 合计 | 519,154 | 519,154 | 48/48 | 13 根 |

所有四份官方日档的摘要与官方校验文件一致。首轮按采集器小时分区核对时，长窗永续末根 K 线缺两笔。另行登记后续一小时，找到了相同 ID 与经济字段的两条接收记录。交易所分别在 00:59:59.922、00:59:59.928 成交，采集器分别在整点后 45.034365、51.031527 毫秒收到。先前未匹配结果与补证分别保留。

按 `received_time < K 线右边界` 对已对账成交做事后截断。13 根 K 线的 266 笔成分在右边界或其后被观察到，最晚约 121.178 毫秒。其中 6 根的收盘价、13 根的成交量在截止时的已收到成交中与最终值不同。开盘价、最高价与最低价在本样本保持一致。四组原始采集文件各自的事件至接收中位数约 124–129 毫秒。这些时钟属于独立采集端，原策略接收与派单仍需对应回执。

本次事后重建能识别截止时缺失的成分。历史发布验收仍需要交易所原始收盘 WebSocket 消息的 `E` 与 `k.x=true`，或有等价可得性证明的独立时间戳。既有两份 Tardis 交易所元数据及其文档都未列出历史 K 线推送频道。公共成交的交易 ID 和接收时间也无法证明私人账户成交与资金费入账时序。[Binance 现货收盘消息格式](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#klinecandlestick-streams-for-utc) · [Tardis 现货历史频道](https://docs.tardis.dev/historical-data-details/binance) · [Tardis 永续历史频道](https://docs.tardis.dev/historical-data-details/binance-futures)

原始文件位于隔离目录 `user_data/crypto_quant/rd_program_20261105_v1/hourly_l2_v001/` 的 `trade-*` 对象。登记、回执、原始对账、后续小时补证、离线复算和逐根差异随代码提交。145 个冻结金融输入通过摘要检查。本轮金融账户数为 0。
