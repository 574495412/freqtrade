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

## 原始精度和盘口重放追加结果

按 [运行前登记](event_book_replay_registration.json) 对上述原始响应执行只读重放，源码与输入 hash 在运行前后验证。`book_replay.py` 使用整数纳秒保留原始 7 位小数接收时间；不按交易所时间重新排序。8 项合成检查覆盖亚微秒顺序、缓冲、序号缺口拒绝、合约 pu 链、数量替换／删除、有限快照边界、断线和交叉盘口。另有真实样本重放结果，二者分别计数。

| 市场 | 有效盘口观测 | 双侧20bps范围完整 | 序号错误 | 首个可用观测（UTC） |
|---|---:|---:|---:|---|
| BTC 现货 | 593 | 593 | 0 | 2025-01-01 00:00:00.8008865 |
| BTC 合约 | 1049 | 0 | 0 | 2025-01-01 00:00:01.0078131 |

全部 6006 条原始消息通过完整精度的接收顺序核验。快照到达前没有生成可用盘口，不把未来收到的快照回填到日初。现货按 U/u 衔接；合约首条匹配快照范围，后续严格要求 pu 等于上一条 u。数量更新是替换而非累加；删除未知价位允许，断线后须重新取得快照。

本次诊断把买侧范围定义为 best bid×0.998 至 best bid，卖侧为 best ask 至 best ask×1.002；没有修改冻结 RD1 风险合同。初始 1000 档快照之外的静态价位未知，后来出现的稀疏更新不能证明中间价位也已完整掌握。合约样本虽可计算已观察到的深度下界，但不得把它写成完整市场深度。冻结 RD1 本身限制的是可见对手盘数量；下界可作为将来保守输入的候选，是否可用仍须连同报价时效、事件来源及完整窗口共同验证，不能单凭“不完整”伪造零容量，也不能借此直接通过经济准入。

原始接收时间与消息 E 的差只作采集诊断，不是已认证网络延迟；不拿 funding 消息的未来结算 T 当发布时间。[逐项结果](event_book_replay_result.json) 链接完整盘口观测文件及 hash，文件位于独立 `event_book_replay_v001` 目录。没有新增网络下载、策略诊断命令、金融回测或订单。

重放规则核对来源：[Binance 现货官方规则](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#how-to-manage-a-local-order-book-correctly)、[Binance 合约官方规则](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly)。这里只验证两份一分钟样本；全历史访问、完整时点规则及私人账户事件证据仍缺失。
