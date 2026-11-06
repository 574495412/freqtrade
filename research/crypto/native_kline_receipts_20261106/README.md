# Binance 原始收盘回执与因子时点验收

在隔离目录登记一个公开采集边界，分别连接 Binance 现货和 USDⓈ-M 永续的 K 线推送。观察 BTCUSDT、SOLUSDT 的 5m 和 1h 原始消息，完整保留交易所事件毫秒、本机接收纳秒和消息正文。采集只连接公共行情流，无账户与订单接口。目标边界为 2026-11-06 15:00:00 UTC，位于冻结 L/S 之外。[现货 WebSocket 字段](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#klinecandlestick-streams-for-utc) · [合约 WebSocket 字段](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)

| 市场与合约 | 正式收盘消息 | 交易所事件时间晚于边界 | 本机接收晚于边界 |
|---|---:|---:|---:|
| 现货 BTCUSDT | 5m、1h | 20 ms | 124.388–124.420 ms |
| 现货 SOLUSDT | 5m、1h | 102 ms | 376.606–376.617 ms |
| 永续 BTCUSDT | 5m、1h | 53 ms | 136.155–136.306 ms |
| 永续 SOLUSDT | 5m、1h | 55 ms | 136.233–136.314 ms |

两条连接共保留 1,256 帧，其中 1,248 帧为未收盘更新。8 条 `x=true` 消息均对应登记边界，消息时间顺序和价量字段通过验收。本机使用应用读取 WebSocket 消息后记录的墙钟，未单独测量其对交易所时钟的偏差。表中差值只描述这次采集，不给冻结历史数据补时刻。

`final_kline.py` 核对流、交易对、周期、正式收盘标记、时间顺序和 OHLCV。给原冻结 `features.py` 构造 `HourClose` 时，以实际回执为可得时间。相同正式内容保留最早接收，决策时已观察到的冲突保留为重复小时并阻断，决策后收到的内容不会改变此前输入。将纳秒转换成冻结函数的微秒精度时向上取整，避免把迟到 1 纳秒的回执提前。合成数据证明同一冻结决策下 F1 与 F2 都会拒绝这一迟到回执。

独立回归还验证原生隔离启动会拒绝捕获证据类别，入场 sizing 会拒绝把捕获盘口和合成账户检查点混用。这两个检查使用合成夹具，与上表的真实公开收盘样本分开。既有策略正常启动仍保持财务运行禁用。新增与既有测试合计 33 项通过、16 个子测试通过，原策略依赖有一条既有弃用提示。

原始帧保存在隔离目录 `user_data/crypto_quant/rd_program_20261105_v1/public_kline_receipt_v001/`。登记、回执、解析、离线核验与测试随代码提交，145 个冻结金融输入通过摘要校验。样本只覆盖 5m、1h 的一个窗口外时点。冻结窗口的历史收盘消息、策略接收与完整执行证据仍待补齐，本轮新增金融账户数为 0。
