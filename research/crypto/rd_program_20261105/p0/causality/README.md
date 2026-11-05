# P0 因果诊断：证据范围与复现

截至 2026-11-06，两次预注册缓存隔离诊断已完成：spot 23 次、futures 25 次内部 backtest；原先两市场各 3 条退出告警在这次有限样本中不再出现。它们是诊断结果，不是收益实验，也不是正常运行模式的全局无未来数据证明。NFI X8 原版 SHA256 仍为 `fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3`，引擎固定为 `c051e9065c5b677f03817dd1f83cb980af1ed684`。未修改策略源码、真实账户、DB 或运行中的 paper 服务。

## 已验证的机制

官方 lookahead 使用 `UTIL_NO_EXCHANGE`。X8 的 `is_backtest_mode()` 仅识别 backtest/hyperopt；而 native engine 在全量退出的 `order_filled` 回调时，`trade.is_open` 尚为真。这一组合使 X8 的 profit-target cache 未在此回调清除。旧工具的 baseline 和全部截断切片共用 userdir，后续切片却回到更早时刻；X8 `exit_profit_target` 对缓存峰值时间没有“晚于当前时刻”的拒绝条件。

`analyze_saved.py` 只解析并调用原版方法 AST，给出保留/清除缓存的模式反例、三条历史 futures 过早退出的充分机制反例。它没有重放交易，不能单独证明旧运行每一刻实际读取了哪个缓存。

`observe_lookahead.py` 保留官方 utility runmode、原版交易与回调输出，为每次 baseline/entry/exit preparation 分配独立空 userdir。它只增加观测和缓存路径隔离；因此称为“官方诊断的编排干预”，不称原封不动的官方命令。已冻结规则快照、依赖、源码、数据 hash；使用离线网络拒绝、统一非阻塞金融 worker 锁、5.5 GiB / 3600 秒资源限制。没有额外再跑 dirty 对照。

## 逐告警结果

以下时间均为 UTC。所有 clean exit slice 与 clean baseline 的目标 trade 完整字段相等；回调比较只去除运行内计数 ID/phase/sequence，保留时间、价格、数量、费用、缓存和返回值。

| 历史告警 | 入场 | 旧 futures 切片提前退出 | clean baseline 与切片退出 | 两条轨迹各比较事件数 |
| --- | --- | --- | --- | --- |
| spot ADA / 0 | 2025-01-19 22:50 | 旧 spot 未保存切片 | 2025-01-20 06:45 | 98 |
| spot ADA / 1 | 2025-01-20 17:40 | 旧 spot 未保存切片 | 2025-01-20 21:50 | 53 |
| spot ETH / 4 | 2025-02-28 02:35 | 旧 spot 未保存切片 | 2025-03-01 06:30 | 338 |
| futures ADA / 0 | 2025-01-19 22:50 | 2025-01-19 22:55 | 2025-01-20 03:05 | 54 |
| futures ADA / 1 | 2025-01-20 17:40 | 2025-01-20 17:50 | 2025-01-20 21:50 | 53 |
| futures ETH / 2 | 2025-01-27 16:40 | 2025-01-27 16:55 | 2025-02-02 00:45 | 1541 |

三条 futures 历史提前退出时刻，clean slice 的原版 `custom_exit` 均返回 `None`。新 baseline 产生的最终缓存与两个市场各自的旧缓存逐字段相等；这些峰值均晚于相应切片的第一次 callback。clean 各阶段没有未来缓存读取，且六个目标全量退出时实际观测到 `is_open=True`、`is_backtest_mode=False`、cache 保留。这将静态机制与实际诊断运行连接起来，但旧运行缺少逐回调 cache bytes，不能声称重新观测到旧历史进程的精确首次分歧。

## 整体对照及局限

- Futures 新旧 baseline 的 12 笔交易完整相等，包括全部订单、数量、价格、标签、资金费。不是仅匹配三条告警的退出时间。
- Spot 旧档案只保存日志/CSV/cache：全部 11 个 checked trade 的 pair/open/close 序列可从 22 个切片日志恢复，并与新运行相等；旧完整 baseline/切片订单从未保存，无法补造完整 parity。历史首次分歧仍标记 blocked。
- 对全部 23 个 clean checked target，除了两市场最后一笔 ADA grinding 订单标签内的数字 ID 尾缀，其他所有字段都相等。源码明确把 entry order IDs 拼入这些标签。机器报告保留原始差异，未将其误报为完整逐字相等。
- 旧 futures 的 24 个切片里 23 个完整交易列表与 clean 有差异；这符合旧缓存影响切片内早先交易的可能性。官方告警只比较目标信号时间，不代表整个切片路径一致。
- 缓存隔离解释的是 utility 编排下的跨切片状态污染；不自动证明正常 backtest/live 的指标、DCA、自定义退出在所有时段均因果有效。正常模式还有不同的缓存清理语义。原策略仍不能凭本结果取得经济或生产准入。

完整输入 hash、逐阶段计数、逐告警 callback、全部 baseline/切片对照见 `isolated_evidence.json`；旧证据与 AST 反例见 `saved_evidence.json`。机器审计脚本默认不覆盖已有输出：

```sh
python3 -B research/crypto/rd_program_20261105/p0/causality/analyze_saved.py --output /tmp/rd-saved-evidence-new.json
python3 -B research/crypto/rd_program_20261105/p0/causality/analyze_isolated.py --output /tmp/rd-isolated-evidence-new.json
```

## 资金费事件边界

已有六账户的小时 mark 会计复核和小额边界敏感性总和，不能替代 5m mark NAV 或真实结算时间证据。原版 minified order export 不含创建时间及每单 funding allocation：两笔不同创建时刻、相同成交时刻的订单能导出完全相同的 minified JSON。`analyze_saved.py` 对未改动的 `Order.to_json` 给出可复现信息不可辨识反例。

本次六个目标 trace 新增 order created/filled 时间和回调前后资金费字段，但不是历史六账户的完整事件追踪，也没有真实 funding 发布/到达时间。原先的同刻 funding/fill 优先级、完整中间状态、5m mark、NAV/DD 误差界仍为 blocked；不得把历史条件敏感性量级当作已证明误差上界。后续需要独立冻结的事件模型和实际可得数据，不扩大本次诊断预算。

## Recursive 的预注册边界

六个官方命令固定 BTC/ETH/ADA × spot/futures、`20261001-20261101`、CLI startup `[199,499,799,999]`。原版会自动加入策略原生 800，因此每命令最多 7 次 indicator preparation（baseline、early indicator check、五个 startup），没有交易 backtest。实际四组不变，800 单列为官方原生 control。

新增观测对 native 指标 DataFrame 的副本调用原版 `ft_advise_signals`，比较实际列的 raw/presence/`==1` 布尔值及标签，不更改 native 输入。主要检查点为窗内 2026-10-31 23:55；早截断为 2026-10-01 00:45。官方 trim 包含 stop 边界，另录 11-01 00:00，避免把它混作窗口内结果。Spot 采用已有 long 数据以覆盖 informative warmup。完整 manifest 及结果必须单独核验；指标差异或单点信号相等都不能当作策略整体通过。

六个命令现已完成，合计 42 次原生指标 preparation、0 次交易回测；`analyze_recursive.py` 从 73 份输入收据重新生成的结果与 `recursive_evidence.json` 完全一致。固定点的向量信号与标签虽相同，但全部为零／空，且指标初始化差异仍在。没有覆盖激活入场、自定义退出或 DCA 路径，因此结论为有限检查完成、策略因果准入未通过。加上两次 lookahead，8 次新诊断预算已全部使用；不得观察结果后换检查点再试。
