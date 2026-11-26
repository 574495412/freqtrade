# 原40币资金研究的存储准入

原40币NFI1000旧账户指标缓存已在原完成流程清理，不能直接用它缩放出500钱包。新500账户需要从固定源码和原数据重新执行；本批解决其磁盘资源缺口，没有运行历史资金账户。

## 实际资源结果

当前旧223币、单月原账户缓存仍存在；它不是新40币20个月的可复用指标输入。本批仅对已完成账户中的843个普通单链接缓存文件进行APFS透明无损压缩，不读取或反序列化pickle对象，不删除研究路径或旧失败记录。

每个文件在原子替换前后核验逻辑SHA256、owner、mode、mtime、精确birthtime、ACL及已有xattr值。跳过硬链接、已压缩文件和未完成账户。每次操作都有fsync后的intent/completed记录，终态再次读取全部843个逻辑文件核对SHA。进程已实际exit0。

文件报告的分配块减少 **9,968,758,784 bytes（约9.28GiB）**。磁盘可用从1,637,609,472到13,734,707,200 bytes；系统同期其他分配或释放也会影响free，不能把整个free差额都归因本批。inode、ctime和压缩flag属于本次存储操作的改变，未用作历史市场first-seen证据。

## 保留的元数据问题

最初的真实缓存副本检查发现ditto没有完全保留原birthtime，原文件没有被替换。读取本机SDK的ATTR_CMN_CRTIME定义后，使用Darwin getattrlist/setattrlist恢复精确创建时间，在副本上重新核验所有元数据及内容后才准入正式批次。原未压缩/压缩合成探针、真实副本、收据和源码均保留。

这不是40币资金回测的完整资源证明：原500钱包还需限定RSS/磁盘/时间守卫，用已有Zstd和LazyTrim/RowBlocks无损存储进行准入；不得把旧223币单月缓存、两币曲线或账户状态target_profit_cache跨钱包复用。原40币历史规则和两个未知资金费时点仍需分别验证，压缩不会改变这些限制。

证据：compression_protocol.json、compression_journal.jsonl、compression_complete.json、completed_sample_compression_probe.json。没有新增策略收益、组合、订单或部署；目标保持活跃。
