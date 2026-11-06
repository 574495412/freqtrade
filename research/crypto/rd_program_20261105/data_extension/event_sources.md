# 历史事件来源实测（2026-11-06）

本次固定探测 BTCUSDT 的 2025-01-01 首分钟与相邻普通日期，另读取两个市场元数据及 Binance 两类日档案。登记先于网络请求；2 worker、单响应 8 MiB、解压 32 MiB、30 秒超时、0 重试，最多 10 次请求含 checksum。只请求公开接口，没有读取凭据、订阅、购买或交易。

| 来源／请求 | 实际结果 | 可以证明的范围 |
|---|---|---|
| Tardis Binance 现货 2025-01-01 首分钟 | HTTP200，3232 条事件 | 2631 trade、600 depth、1 generated snapshot，有采集端时间 |
| Tardis Binance 合约同一分钟 | HTTP200，2774 条事件 | 1660 trade、1053 depth、60 markPrice、1 generated snapshot，有采集端时间 |
| 两市场 2025-01-02 首分钟 | 均 HTTP401 | 服务明确要求非首日数据的授权访问；没有尝试绕过或查找密钥 |
| 两市场公开元数据 | HTTP200，固定 36+40 个交易对均列出 | 元数据声明，不证明每个日期、频道无缺失 |
| Binance BTC bookDepth 2025-01-01 | HTTP200，官方 checksum 通过，28800 行 | 字段 timestamp/percentage/depth/notional，percentage 为 ±1..5；无逐档价格或 20bps 数量 |
| Binance BTC bookTicker 同日 | HTTP404 | 仅证明这一个指定档案不可取，不能推广为整个数据类型不存在 |

原始压缩响应、HTTP 状态与头、错误响应、checksum、首次/完成时刻都保存在 `user_data/crypto_quant/rd_program_20261105_v1/event_source_probe_v001`。Git 保存 [运行前登记](event_source_probe_registration.json)、[下载代码](probe_event_sources.py) 和 [实际结果与逐币元数据](event_source_audit.json)，不改写原 P1。

## 尚未完成的准入核验

公开样本只是每市场一分钟，既不覆盖固定 L/S 全窗，也不覆盖 F1 的 168h warm-up。原始 local timestamp 有 7 位小数，下载探测的 Python datetime 摘要截为微秒；原始字符串完整保留，但不能把该摘要的单调性检查称为更高精度的事件排序验证。下一步按完整原始精度核验并重建盘口序列，验证快照边界和 20bps 覆盖；当前尚未完成这些检查。

供应商时间是其采集端到达时间，不能自动变成策略机器历史接收时间。其公开文档说明各频道无统一交易所顺序，因此还须明确研究事件时钟以及同刻歧义，不能把采集顺序误称真实撮合／资金费结算顺序。历史私人订单、持仓、现金和 funding 对账也不会由公共市场数据自动补齐。

历史规则还有独立缺口：供应商 Instruments Metadata 文档只保证已跟踪合约乘数变更的完整性，其他字段（含 tick/amount increment）是 best effort；即使取得订阅，也不能直接认定完整历史规则已闭合。本次未调用需要授权的 Instruments API，未验证任何账户权限。

这些发现把“无已验证的可用数据”具体化为“已取得可复查小样本，普通日期访问受限，完整性与事件时钟仍待验证”。F1/F2 经济准入保持 false，新增金融运行 0。已询问用户是否存在可读取的合法历史数据包；不要求在聊天中提供密钥。

## 官方资料

- [Tardis Binance Futures 来源、频道与快照机制](https://docs.tardis.dev/historical-data-details/binance-futures)
- [Tardis Binance Spot 来源与频道](https://docs.tardis.dev/historical-data-details/binance)
- [Tardis HTTP API 分钟切片及时间语义](https://docs.tardis.dev/api/http-api-reference)
- [Tardis 数据与跨频道顺序说明](https://docs.tardis.dev/faq/data)
- [Tardis 历史规则元数据的覆盖限制](https://docs.tardis.dev/api/instruments-metadata-api)
- [Binance 公共数据与 checksum](https://github.com/binance/binance-public-data)

文档描述与本次实际请求结果分开记录；不是收益或可交易性认证。
