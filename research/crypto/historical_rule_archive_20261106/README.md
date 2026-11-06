# Binance 历史规则存档核验

通过 Internet Archive 查询冻结窗口内 Binance 两个公开 `exchangeInfo` 端点。8 次 Availability 请求均返回 429。4 次精确 URL 的 CDX 索引请求返回 200，长窗现货返回 48 条、短窗现货 1 条、长窗合约 14 条、短窗合约 0 条。63 条索引记录中 58 条记录的是 HTTP 451，剩余 5 条为 HTTP 200 JSON。查询有 50 条上限，结果只代表本次查询返回的范围。[Internet Archive API](https://archive.org/help/wayback_api.php)

对 5 条候选各请求一次精确时刻的原始重放，全部返回 HTTP 200。没有跟随重定向。5 个 `Memento-Datetime` 与请求和索引时刻一致，CDX 的 Base32 SHA-1 均与响应内容匹配，其中第一份匹配解压内容，其余四份匹配收到的压缩内容。原始响应另外留存 SHA-256。

| UTC 存档时刻 | 市场 | 窗口 | 冻结交易对规则字段完整 |
|---|---|---|---:|
| 2025-04-26 00:58:18 | 现货 | L | 36/36 |
| 2025-12-09 11:16:42 | 现货 | L | 36/36 |
| 2026-10-27 04:35:05 | 现货 | S | 36/36 |
| 2026-02-17 04:13:28 | USDT 永续 | L | 40/40 |
| 2026-05-10 10:30:16 | USDT 永续 | L | 40/40 |

188 条交易对时点记录含交易状态、价格步长与范围、限价数量步长与范围、最低名义额以及市场单数量过滤器原字段。连续两份现货快照分别观察到 3 和 10 个交易对的价格步长变化，合约两份快照观察到 5 个。对比使用十进制数值，忽略尾随零差异。各变动的旧值、新值和端点时刻见[机器可读核验](verification.json)。

- BTCUSDT 永续的最低名义额在两份合约快照分别为 100 和 50 USDT。公告给出 2026-04-14 06:30 UTC 开始调整和约四小时的实施时长，逐订单实际切换时刻仍缺回执。[Binance BTC 最低名义额公告](https://www.binance.com/en-NG/support/announcement/detail/10999fd17dc045de801c0c78ab29e6fc)
- SUSHI/USDT、NEO/USDT 和 GRT/USDT 的现货价格步长变化与公告前后值一致。公告分别安排在 2025-08-21 05:00 UTC、2025-10-14 07:00 UTC 前完成调整。两份现货公告均说明存量订单沿用原步长。[8 月现货公告](https://www.binance.com/en/support/announcement/detail/2c7fd0e087de4a9eb16798b9ed92465d) · [10 月现货公告](https://www.binance.com/en/support/announcement/detail/d55ff705e41b45c881a4e1db3fd96698)
- DYDXUSDT 永续价格步长从 0.001 变为 0.0001。两份快照均显示 `TRADING`，公告另记录 2026-04-16 07:30 至 07:31 UTC 暂停新挂单、撤单和改单。[Binance DYDX 合约公告](https://www.binance.com/en/support/announcement/detail/3208154ca9544e22a999808c06f26cd7)
- 两份合约快照均显示 SOLUSDT 永续限价单最低数量与步长为 0.01 SOL。早于 2025-04-09 的 SOL 合约快照仍缺。[SOL 公告核验](../historical_rules_20261106/README.md)

存档时间表示第三方捕获公开响应的时间。响应的 `serverTime` 和原始 HTTP `Date` 单独保留。各快照之间以及 L 窗口开头仍缺逐时规则版本，合约 S 窗口在本次索引查询中无对应记录。逐笔执行准入还需要规则生效区间、策略可得时刻、同步盘口及成交与资金费回执。旧 24 个成本模型结果仍以原登记口径解释。

索引和重放分别预登记，原始响应留在隔离目录 `user_data/crypto_quant/rd_program_20261105_v1/exchangeinfo_archive_probe_v001/`。提交包含登记、索引和重放回执、冻结币池字段及离线核验程序。运行 `verify_snapshots.py` 中的 `audit()` 可在保留原始文件的工作区离线复核 17 个响应和 145 个冻结金融输入。新增金融账户数为 0。
