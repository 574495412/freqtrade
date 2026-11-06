# 历史交易规则核验

冻结 `exchangeInfo` 规则收据采于 2026-11-05。Binance 的公开公告确认 L 窗口内存在数量门槛和名义额门槛变更。

| 合约 | 规则字段 | 公告旧值 | 公告新值 | 公告安排 |
|---|---|---:|---:|---|
| SOLUSDT 永续 | 最低交易数量 | 1 SOL | 0.01 SOL | 原定 2025-04-02 09:30 UTC，后推迟至 2025-04-09 09:30 UTC |
| BTCUSDT 永续 | 最低名义额 | 100 USDT | 50 USDT | 2026-04-14 06:30 UTC 开始，公告预计约四小时完成 |

公告中的最低交易数量与数量步长是不同字段。BTC 的开始时刻与实际逐标的切换时刻也需分别核对。Binance 说明最低名义额可不经预告调整，因此公告之间仍需逐时点规则回执。[SOL 原公告](https://www.binance.com/en-IN/support/announcement/detail/62e7f20e3f2d4313ab7b2147f51065c6) · [SOL 延期公告](https://www.binance.com/en/support/announcement/detail/b2cecfd1006947e18bf029c11d19c351) · [BTC 公告](https://www.binance.com/en-NG/support/announcement/detail/10999fd17dc045de801c0c78ab29e6fc) · [Binance 合约规则](https://www.binance.com/en-AE/support/faq/detail/360033161972)

对六个已封存的合约长窗账户执行只读筛查。核对原报告和原生导出的哈希后，仅取 1x 多头的首次入场，按原生模拟的整笔数量和价格计算不含费用的名义额。公告锚定区间从发布或延期日之后的 UTC 日界开始，截止公告安排的调整时刻，两个合约在该区间均无对应入场。将旧值向前用作敏感性筛查，得到 34 条低于 1 SOL 的 SOL 入场和 6 条低于 100 USDT 的 BTC 入场。这 40 条是跨账户、跨成本情景的模拟记录，逐笔历史规则仍待核对。现有档案缺少这些订单的实际历史提交与交易所接受回执。

11 月 1 日 BTC 和 SOL 的 11,316 条买卖报价，在算术上均与 11 月 5 日规则快照的价格和数量网格一致。网格一致性只覆盖这一分钟的观测值。最低数量、最低名义额和历史规则版本需独立取证。

三个 Tardis Instruments 公共请求均返回 401。该接口文档要求授权，并把历史价格与数量增量变更标为尽力记录。交易所公告正文通过网页工具核对，命令行对三个公告的请求返回 202 空响应，已分别保留回执。已核验的正文来源和字段见 `notice_facts.json`，请求和订单筛查明细见 `result.json`。[Tardis Instruments Metadata](https://docs.tardis.dev/api/instruments-metadata-api)

恢复准入需要覆盖冻结币池和窗口的合法可读规则版本，含各字段的有效区间、已知时刻及过渡证据，再与可执行盘口和账户事件对账。本轮新增金融运行数为 0，145 个冻结金融输入均通过哈希核验。历史规则准入仍待完成。
