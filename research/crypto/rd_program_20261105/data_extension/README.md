# 独立 5m mark 数据补充（不改写 P1）

P1 完成后，Binance 只读工具成功取得 BTCUSDT 2025-01-01 的 3 根 5m mark；官方月档案的 SHA256 校验和通过，前三根 OHLC 与工具逐字段一致。小样本只证明可访问该段数据，不能证明全币池完整性。

`mark5m_plan.json` 在批量下载之前冻结固定 40 币和 L/S 窗口，共 1000 份官方档案：L 的 20 个月、2026-10 月档案、2026-11-01 至 04 的 4 份日档案，各覆盖 40 币。2 个下载 worker、每文件 10 MiB、每个解压 CSV 32 MiB、空闲磁盘至少 10 GiB；超时/限流/5xx 最多 2 次技术重试，404 等保留失败。没有额外金融回测或参数候选。

每份档案保存下载和官方 checksum 收据，验证 OHLC 有限且为正、价格上下界、完整 5m 网格、唯一时刻、close_time=open_time+299999。任一缺失不插值、不删币；只有全部通过才生成 80 个独立 L/S×40 币 feather 文件。新文件位于新目录，旧 P1 数据、配置、运行结果和评分表保持原有身份。

这些是历史 mark candles，不提供历史本地接收时间，也不是可成交 bid/ask、盘口深度或资金费真实结算顺序。下载成功只补充估值价格来源，不能自动批准 F1/F2 或 RD1 经济试验。后续需单独核对旧小时 mark、冻结持仓重估和数据可用性，完整保留前后模型差异。

官方来源（读取于 2026-11-06 上海时间）：

- [Mark Price Kline REST API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)：支持 5m、起止时刻、单次上限 1500。
- [Binance 公共数据站](https://data.binance.vision/)；试取文件 `data/futures/um/monthly/markPriceKlines/BTCUSDT/5m/BTCUSDT-5m-2025-01.zip` 及 `.CHECKSUM`。
- [官方历史数据说明](https://www.binance.com/en/blog/futures/421499824684901131)：页面发布于 2020 年，说明曾要求白名单账户通过 API 下载盘口；本任务未读取凭据或验证任何账户权限。
- [官方流动性图表说明](https://www.binance.com/en/support/faq/detail/afd1ccded7ca41108b281c927fefa3a0)：展示按 1%–5% 区间聚合的深度；不能据此推断 RD1 所需 20 bps 内的实际盘口数量。

## 已完成的恢复与估值补充

初次 1000 份档案中 920 份通过，80 份网格不完整。当时失败载荷没有写盘，因此原结果的 `raw_archives_preserved=true` 只适用于成功的 920 份；原记录保留，不回写成全部成功。随后独立登记、重新保存全部 80 份异常原始档案，逐币确认缺 2026-06-29、10-28、10-29，再以 80 次精确官方 API 查询补齐 34,560 行。API 保存的是结构化 MCP 响应，不是原始 HTTP 字节。没有插值或替换档案中的既有观测。

80 份 L/S 文件共 7,153,920 行，596,160 个小时边界的 mark OPEN 与旧输入完全相等。[数据审计](mark_data_audit.json) 记录失败纠正、恢复和各文件 hash。程序集和原始资料在独立研究目录保存，冻结的 P1 输入未变。

以 [独立估值登记](mark_valuation_registration.json) 冻结代码和 3,548 份输入收据后，完成全部 16 个既有合约单元的持仓重估；5 项边界测试通过。[重估结果](mark_valuation_result.json) 保留原现金／资金费模型，仅替换未实现损益与退出费储备的估值价格，不能证明真实结算顺序或可成交收益。新增金融运行数为 0。

最新结论见 [交付报告](../../../../docs/crypto-factor-delivery-20261106.md)。5m mark 缺失已经解除，历史发布／接收时间、盘口和事件顺序等候选准入缺口仍在。
