# 资金费缺失时点：源证据复核，继续UNKNOWN

AXSUSDT及TRBUSDT在2026-06-24 04UTC的预期资金费行仍缺失，没有补零或更改旧账户。两份官方6月ZIP均179行，下载字节与公开CHECKSUM匹配；原始CSV、REST、已连接Binance路由的12条邻近记录一致。04UTC整个小时无记录，毫秒抖动没有隐藏该行。

未取得精确历史日程变更或认证客户结算证据。邻近记录仍写4小时并不足以证明缺席时点实际不存在；三种读取均来自同一交易所，不作为三个独立结算权威。当前采集版本不认证历史first-seen。完整源码、CRC、SHA、记录时间及费率逐项核对，见source_audit.json和source_receipts.json。

没有新金融账户、策略更改、订单、部署或收益主张。后续分户原生暴露审核发现六个旧NFI账户在该时点无持仓，而八个lowvol/skew账户均持仓；该差异只适用于对应的固定历史模型，不消除原始费率UNKNOWN，也不自动适用于任何新账户。

[完整账户和精度审核](../nfi40_account_admission_20261117_v1/README.md)；[来源审核](source_audit.json)；[原始回执](source_receipts.json)；[冻结协议](protocol.json)；[文件清单](artifact_manifest.json)。
