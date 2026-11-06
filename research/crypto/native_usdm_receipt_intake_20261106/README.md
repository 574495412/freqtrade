# Binance USDⓈ-M 私有回执离线接入

`receipt_intake.normalize_batch(bundle, pairs=...)` 接收单账户、单连接的一组已脱敏财务回执。输出可交给现有隔离 `AccountReplay` 做回溯算术。适配器不启动原生策略或账户，调用方按已冻结币池传入交易所 symbol 到研究 pair 的映射。

## 输入与核对

- 外层需显式声明 `contract=binance_usdm_usdt_isolated_oneway_long_1x`、`account_id`、`connection_id`、`evidence_class` 和 `financial_receipts`。每条包含从 1 开始的财务子序列 `sequence`、UTC `received_at`、外部绑定的 `campaign_id`、原字段结构的 `ws` 和配对 `rest` 对象。子序列检验只能发现交付清单里的序号缺口。连接身份、原始捕获内容及删减记录须由交付者另行证明。
- `ORDER_TRADE_UPDATE` 的 `TRADE` 与 `/fapi/v1/userTrades` 逐笔核对 symbol、订单及成交 ID、方向、仓位模式、数量、价格、USDT 手续费、maker、已实现盈亏字段和交易毫秒。订单原数量须一致，累计成交必须从该订单第一笔连续相加，终态须与累计量吻合。采用单向多头的清洁起点，缺失初始仓位、空头、反手、强平、ADL 和结算成交超出范围。交易所报告的已实现盈亏保留供核对，回放按成交独立计算。 [Binance 账户成交接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#account-trade-list-user_data)
- `ACCOUNT_UPDATE` 的 `FUNDING_FEE` 与 `/fapi/v1/income` 核对单一 USDT 余额、逐仓仓位 symbol、交易毫秒、带符号金额及独立 `tranId`。只有在 `bc` 和收入记录金额完全吻合时才接入。负收入是费用，正收入是入账。平仓后同一交易周期仍可凭独立回执入账，交易周期归属必须由交付者提供。余额转移、余额以外的资金费形态及无仓位明细的回执需要单独证明。 [Binance 账户流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams) · [Binance 收入接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#get-income-history-user_data)
- 分别保留事务、发布和本地接收时钟。本批保守要求成交内外事务时钟一致，事务与接收均不得回退，同类发布时刻须前进。任意两个财务事件共享事务毫秒时，整批拒绝。排序字段、处理延迟和 REST 记录身份都无法补出该批的跨类成交与资金费先后。 [Binance 消息顺序规则](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)
- REST 是事后交叉验证。输出的 `received_at` 是 WS 捕获时间，不能作为 REST 已知时间或历史信号可用时间。输出保留 `sequence_basis=unknown`，认证、完整起点和经济资格始终为 false。

[登记](registration.json)固定了官方 Binance Go SDK USDⓈ-M v1.24.0 的提交、六个 schema 源文件的哈希、隔离回放和冻结研究输入。合成测试用例提供可执行的输入示例。

## 历史恢复条件

两种常规私有 REST 查询当前只覆盖近三个月。冻结长窗的较早记录须依靠已有的私有原始存档，或由账户所有方核实历史导出权限与覆盖。REST 历史导出可以帮助对账成交和资金费金额，交易所用户流原始帧、连续接收日志、订单和交易周期绑定、账户初始状态及同毫秒排序证据仍需分别提供。任何实际档案接入都需验收账户归属、源文件哈希、缺口及跨连接连续性。历史正式 K 线接收、连续 20bps 深度和历史规则证据仍是独立条件。 [Binance 成交历史查询范围](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#account-trade-list-user_data) · [Binance 收入及历史下载接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)

[验收回执](verification.json)记录的回放均为合成测试。实际私有档案接入、完整 RD1 经济账户和新增金融账户均为 0。已封存的 24 个固定成本模型账户全部亏损，沿用原准入门槛和淘汰结论。
