# 历史价格步长公告与成交交叉核验

将已存档的 `exchangeInfo` 前后值与 Binance 公告配对。本轮新增 8 个现货交易对的公告旁证。另复核此前确认的三个现货与一个永续变更。每个案例只观察公告所涉 UTC 小时及其前一小时，取得官方逐笔日档、SHA-256 校验文件和独立小时采集数据。[规则快照存档核验](../historical_rule_archive_20261106/README.md)

| 公告完成期限 UTC | 现货交易对 |
|---|---|
| 2025-08-21 05:00 | NEO、SUSHI |
| 2025-10-14 07:00 | GRT |
| 2026-05-20 05:00 | AVAX、LINK、SAND |
| 2026-06-25 05:00 | GALA |
| 2026-07-22 05:00 | FIL |
| 2026-08-20 05:00 | EGLD、IOTX、SNX |

现货公告使用完成期限口径。11 对在登记的截止前时段未观察到偏离旧步长的成交，截止后均出现只能由更细步长表示的成交。样本内第一笔细步长成交晚于公告期限约 5.459–434.386 秒。11 笔均在独立采集档按相同交易 ID、价格、数量、maker 标记和时间桶匹配，保留了采集器接收时间。首笔观察界定了该档案中细步长成交最晚已经出现的时刻，规则实际首次生效及旧挂单存续仍需交易状态和订单回执。[2025-08 公告](https://www.binance.com/en/support/announcement/detail/2c7fd0e087de4a9eb16798b9ed92465d) · [2025-10 公告](https://www.binance.com/en/support/announcement/detail/d55ff705e41b45c881a4e1db3fd96698) · [2026-05 公告](https://www.binance.com/en/support/announcement/detail/ac3fad5bf9c041cdb25a77347c17a2e2) · [2026-06 公告](https://www.binance.com/en/support/announcement/detail/0be6f26461354fd29e3d9c9cc349a7d5) · [2026-07 公告](https://www.binance.com/en/support/announcement/detail/c2accbcabd2246219af63759515326e6) · [2026-08 公告](https://www.binance.com/en/support/announcement/detail/b025495c95c94ce2bdd66338496fc487)

DYDXUSDT 永续公告的变更和挂单、撤单、改单暂停区间为 2026-04-16 07:30–07:31 UTC。逐笔日档在截止前有 4,776 条价格偏离两版步长，暂停区间内另有 13 条。追加比对仅用聚合日档中首尾交易 ID 相等的记录，要求与逐笔日档在时间、数量和 maker 标记一致。

- 截止前有 2,374 笔匹配身份，其聚合价均为逐笔价的 1,000 倍。其中 2,321 笔独立采集价也与聚合价一致。
- 暂停区间内 13 笔均在聚合日档与独立采集档出现，后两者价格一致，均为逐笔价的 1,000 倍。例子为 ID `563384305`，逐笔价为 0.000106，聚合与采集价为 0.106。
- 暂停区间后匹配的 757 笔逐笔价和聚合价一致。07:31:29.118 UTC 出现首笔符合 0.0001 且偏离 0.001 的成交。逐笔与聚合档匹配该身份，独立采集档未确认这笔首见证据。同期 24 根冻结 5m OHLCV 与官方 K 线日档逐项匹配。

DYDX 原始逐笔价保留为来源冲突。上述成交回执无法确定交易状态切换或是否接受了新单。资金费和金融结果仍按原封存证据解释。[DYDX 调整公告](https://www.binance.com/en/support/announcement/detail/3208154ca9544e22a999808c06f26cd7) · [官方日档字段](https://github.com/binance/binance-public-data/)

首次下载器只接受以 `.parquet` 结尾的对象路径，拒绝了 18 个同名 `.parquet.zst` 变体。核实存储主机和完整对象路径后重新登记，18 个外层 Zstandard 文件全部获取并限长解压。首轮拒绝保留。首次交叉复核还遇到路径检查回执缺 `kind` 字段，修正可选字段读取后复算，失败登记单独保留。[供应商 REST 对象格式](https://www.cryptohftdata.com/docs/rest-introduction)

原始响应留在隔离目录 `user_data/crypto_quant/rd_program_20261105_v1/hourly_l2_v001/` 的 `tick-*` 对象。145 个冻结金融输入通过哈希检查。连续历史规则准入仍未通过，本轮新增金融账户数为 0。
