# RD1 隔离执行适配：开发中

接续用户新建的持续目标，补原生订单生命周期与原子风险预留；不改写冻结 RD1、P1 输入、因果诊断预算或运行服务。

本阶段 `reservations.py` 实现预留层。每个隔离账户有单写者文件锁和进程内互斥锁，先让冻结 RD1 对真实快照检查，再把未决预留作为额外挂单计算余额、gross/net、单币、相关簇和数量上限。虚拟挂单不写入 RD1 的真实快照游标，避免同游标内容冲突。预留写前日志和主状态持久化成功后才返回记录。

相同请求 ID、相同提案只返回旧记录；不同提案复用 ID 拒绝。返回记录始终 `submission_authorized=false`，包括重启后查询旧请求；它不是可调用交易所的授权 token。持久化异常后实例不可再用。中断于日志提交与主状态替换之间时，重开恢复日志，仍保留风险占用。

当前保留全部未决数量，没有释放、提交、确认或执行 API。订单已实际进入快照时可能保守重复占用，尚不适合驱动交易。下一阶段须实现：提交前重新检查新鲜快照与风险锁、持久 single-attempt 状态、确认与未知结果、部分成交的预留转移、撤单竞态、最终成交／持仓对账。超时、未见订单、重启均不能单独证明请求未成交。原生 `freqtradebot.py` 的 `execute_entry`、`handle_cancel_enter`、`update_trade_state` 是待衔接的生命周期入口，当前没有修改或调用它们。

已通过 10 项合成单元／故障测试（`test_reservations.py`），覆盖并发、幂等、冲突、簇限制、多空 gross、重启、单写者、写盘异常、日志恢复、缺报价和返回对象隔离。测试不是真实行情、沙盒或生产验证，没有金融回测或订单。

```sh
python3 -B -m unittest discover -s research/crypto/rd_program_20261105/execution_extension -p test_reservations.py -v
```

## 数据可得性初核（2026-11-06 上海时间）

读取了已组装的 BTCUSDT L 窗 mark 文件：175104 行，列为 `date/open/high/low/close/source_id`，无 `received_at`。这是此文件的实际 schema 核验，不把一个文件的检查扩大为所有来源都不存在时间证据。

重新核对 [Binance 官方合约市场数据说明](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)：`/fapi/v1/depth` 请求参数为 symbol、limit；返回含事件输出时间 E、交易时间 T 和价格／数量档位。未列历史起止时间参数。由此判断该接口本身不能按旧窗口回补盘口；这不是对全部历史供应商或下载服务的不可得证明，仍需继续调查。当前请求返回的 E/T 也不能代替过去的首次本地接收时间。

历史数据准入、原生生命周期适配、事件重放和 F1/F2 配对实验均未完成。持续目标保持 active。
