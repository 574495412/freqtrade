# NFI 14-slot futures release

The candidate retains the existing NFI X8 entries and exits and the 12-way wallet sizing proposal. It raises concurrent capacity to 14 and caps cumulative entry margin on pure short Rebuy tags at twice the initial fill. The live and paper adapters share those callbacks.

Use the existing deployment's Git remote to fetch a specific reviewed commit. An isolated detached worktree holds the release code. Run `cutover.py --activate` from that worktree on the existing host as root. The command reuses the original systemd service, Python runtime, account credentials, and SQLite ledger.

The operator entry checks the original release, candidate resolution, current snapshot and exchange-to-ledger agreement. It stops only the futures service and takes an integrity-checked SQLite backup. Already open positions stay in the original ledger and go to the new worker. If active orders are detected before the stop, the command aborts before changing the service. A failure after stopping removes its own systemd override and resumes the original worker when exchange-to-ledger agreement can be re-established. The backup is evidence for recovery and never restored as part of an automated rollback.

The cutover receipt records the new version's fresh running heartbeat and exchange reconciliation. The existing notifier still reads the archived 12-slot config for its slot display. Use the active worker log and cutover receipt to verify the 14-slot setting.

## 本地模拟运行

使用从 `80917bd14` 分离检出的原生 Freqtrade，继承 1,000 USDT 虚拟金、逐仓 3 倍和单边 8 bp 纸盘配置。独立日更名单来自固定选择器的公开行情请求。无交易凭证、API 服务或外发通知，数据库保存在 `user_data/deployment/nfi80-rebuy-cap-paper-20261126/account`。`refresh` 只写入独立目录，名单到期时继承的日界检查停止新首仓。

```sh
.venv-paper/bin/python -B -m deploy.nfi80_rebuy_cap_paper.paper refresh
.venv-paper/bin/python -B -m deploy.nfi80_rebuy_cap_paper.paper prepare
.venv-paper/bin/python -B -m deploy.nfi80_rebuy_cap_paper.paper run
```

首轮原生启动的通知字段校验和第二轮文件路径错误已修正，失败日志保存在同级归档目录。原生额度、配置和日更策略回归共 19 项通过。当前观察为本地进程，无定时刷新或开机自启。

2026 年 11 月 26 日 20:44 上海时间，本地模拟进程在首轮处理后写入 `RUNNING` 心跳，策略版本为 `v18.0.146-flatboost1.25-paper-daily-14slots-short-rebuy-cap`。已加载 80 币，SQLite 完整检查通过，账本有 0 笔交易及 0 笔订单。观察收据保存在 `user_data/deployment/nfi80-rebuy-cap-paper-20261126/startup-observation.json`，本次名单有效至 11 月 27 日 08:00 上海时间。

## 历史回测与上线依据

近两年连续账户在单边 15 bp 费用情景下，普通 14 仓和空头加仓上限版的完整账户净收益分别为 +3,574.95% 和 +3,993.63%。单边 8 bp 情景下，两者分别为 +4,775.48% 和 +4,773.62%。双成本准入结果为未通过。 [两档原生账户](../../research/crypto/nfi80_extended_windows_20261126_v1/short_rebuy_14_wallet_comparison.json)

在 2024 年独立的 15 bp 回放中，上限版最大回撤为 51.74%，归档 12 仓为 45.94%。上限版年度最大回撤与普通 14 仓相同。 [年度账户与逐笔核对](../../research/crypto/nfi80_extended_windows_20261126_v1/short_rebuy_cap_2024_verified.json)

按本次选型采用的近两年窗口、15 bp 费用和完整账户累计净收益排序，上限版的期末净值为 40,936.29 USDT，在三个同条件且已对账的账户中居首。实盘状态以部署收据和交易所对账为准。
