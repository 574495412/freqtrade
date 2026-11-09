# NFI X8 合约配置与通知准备

2026-11-09：用户最新选择为**仅合约**，原版 NFI X8，沿用现有带单 API、合约钱包可用资金和企业微信。此目录是配置准备、只读诊断与通知传输的本地扩展，不是新的策略引擎，也不是实盘上线回执。

## 币池、杠杆与仓位数量的来源

本目录此前准备的是“固定 NFI X8 原版源码 + 本地研究账户配置”，不能称为完整照搬作者运行配置。作者固定版本的 README 建议 6–12 个并发交易、unlimited stake 及 40–80 个币，并提到按成交量选币；这些是建议范围。作者 `configs/exampleconfig.json` 的确使用 6 仓、unlimited stake 和 0.99 可交易资金比例，因此 6 仓有作者示例依据，但不是策略唯一允许值。

普通、Rebuy、Grind 的源码默认杠杆均为 3.0；本地 `deploy/configs/futures.json` 通过作者支持的 `nfi_parameters` 接口将三项覆盖为 1.0。40 个具体币来自本地既有历史数据与可部署市场的研究交集，采用 StaticPairList，不是作者指定的完整币池。作者同版本另有静态与成交量筛选配置，不能仅凭币数相似就视作同一选币规则。

今后的策略比较应先固定各候选的完整运行配置，并区分作者默认复现、统一风险对照、本地优化三类含义；不能把 40 币/1×/6 仓作为所有策略的固定前提。改变币池、杠杆或槽位后，既有收益不能直接移用。3×是源码默认值，不构成对该账户的适用性判断。本次仅校正来源说明，没有修改现有账户参数或启动主网交易。原始作者 Git 对象、当前配置及研究协议复核见 `research/crypto/nfi_parameter_provenance_20261109.json`。

## 选择含义更正

用户要求按可比净收益选择，而非默认原版。此前保留原版是模拟运行回退决策，不是收益最高的证据。本次只准备原版的状态不等于完成了该最高收益选择目标。

已重新核对 2025-01 至 2026-08、40 币、1,000 USDT、1×、15 bps 单边成本的三套既有原生成交账户及五分钟曲线：原版 +135.66% / 回撤 8.61%，固定 Top Coins 首仓 1.25 倍组 +161.84% / 8.98%，按多头占用调整的 1.5 倍组 +151.52% / 8.81%。三个曲线哈希和收益/回撤复算一致，固定组原生回测归档也已核对。固定放大组与正在模拟的 AddCap 限制追加版是不同候选。

这些结果只说明该历史窗口有比原版更高收益的候选。固定组在 2024 复验中仍未通过预先冻结的增量回撤门槛，不能将失败记录改成通过；也不能把不同杠杆、窗口的最高值当成可上线最优解。本次未改主网策略配置或启动交易，既有命令仍对应原版。复核范围和曲线凭证见 `research/crypto/selection_correction_20261109.json`。

## 当前结果

- 固定来源：Freqtrade 上游 `c051e9065c5b677f03817dd1f83cb980af1ed684`；NFI X8 来源 `1af55971d3a64703ec449078e09fdec257d0d3c6`。本次没有修改既有引擎、策略、纸面配置或账本。来源和文件校验沿用 `deploy/launch.py` 与 `deploy/release.json`。
- 官方只读 REST 检查确认：现有密钥允许合约交易、属于合约带单账户；可用 USDT 为 `497.74760163`，零持仓、零挂单。40 个配置币种均在返回的 754 个带单交易对白名单内。结果有时间戳，不能代替启动时重新核对。
- 准备配置：逐仓；普通、补仓、grind 三类杠杆均为 `1.0`；最多 6 仓；原有 40 币；`stake_amount=unlimited`、`tradable_balance_ratio=0.99`。快照预算为 `492.7701256137 USDT`，不是固定投入承诺，也不表示单笔投入这一金额。保留原策略加减仓逻辑。
- 私有文件位于 `user_data/deployment/live-prepare-20261109/`，凭据与配置均为 600 权限并被 Git 忽略。基础配置不含交易密钥，另有私有 `credentials.json` 配置覆盖文件。使用全新合约数据库路径，没有复制模拟仓位。
- 配置为 `dry_run=false`、`initial_state=stopped`，API 服务关闭。仅通过原生 StrategyResolver 和配置 schema 的离线检查；没有实例化 Exchange 或 FreqtradeBot。**没有启动实盘进程、下单、改持仓模式、改杠杆、划转资金，也没有安装实盘交易自启动任务。**
- 既有四个模拟服务仍在运行。现货实盘配置已归档为私有 `spot/config.not-selected.json`，不会在本次选用。

## 阻塞与人工接管

首次核验时账户 `dualSidePosition=true`，当前原生 Binance 实现明确拒绝双向持仓模式，见 `freqtrade/exchange/binance.py` 的 `additional_exchange_init`。用户随后自行改为单向；最新只读复核 `dualSidePosition=false`，可用 USDT 仍为 `497.74760163`，零持仓、零挂单，40 币白名单通过。复核时间及原生离线配置结果见 `one-way-recheck.json`；旧 `receipt.json` 保留首次核验事实。双向模式这一兼容性阻碍已解除，尚未实例化交易所客户端或验证真实成交。离线 STOPPED 配置并不意味着运行交易进程没有初始化副作用，因此本次未运行交易 CLI。

真实资金交易必须由用户本人启动。本目录不提供自动启用交易脚本。

原有上线资格尚未通过：`deploy/release.json` 仍为 `production_eligible=false`；此前官方 lookahead 工具报告的合约 3 个退出时间异常未解决，见 `docs/research-20261105/crypto-wide-comparison-20261105/official-lookahead-interpretation-20261105.md`。离线配置检查与通知测试没有消除该问题。

原策略静态 `stoploss=-0.99`，`stoploss_on_exchange=false`；策略另有内部退出规则，不能把 1×解释成严格亏损上限。此配置准备没有添加或验证交易所保护止损。

## 用户说明为官方测试账号后的环境复核

用户随后说明希望启动的是币安官方测试账号。本次按这个目标只读复核，没有把该说明当作主网余额属于虚拟资金的证明：现有配置没有 `demo_trading` 或 CCXT URL 覆盖；此前认证成功的合约账户接口为 `https://fapi.binance.com/fapi/v3/account`。币安官方文档将主网 `fapi.binance.com` 与测试网 `demo-fapi.binance.com` 区分。

同一现有密钥访问官方测试网 `GET https://demo-fapi.binance.com/fapi/v3/account`，返回 HTTP 401、交易所代码 `-2015`。这表示本次测试网鉴权失败，不能据此判断是密钥、IP 还是权限原因；没有读取到测试网余额，更没有启动测试网交易。脱敏回执见 `demo-environment-check.json`。原始响应留在私有忽略目录。

当前固定版本 `freqtrade/exchange/binance.py` 明确设置 `supports_demo_trading=False`；现有 CCXT 虽能离线生成 Demo URL，但不等于 Freqtrade 已支持该执行环境。因此不能把主网配置改个名称当成测试网，也不能仅切换开关就声称已完成原生适配。后续需要测试网凭据的本机配置路径，随后在原生接口完成隔离适配与验证；这与授权启动真实资金交易是不同范围。现有主网准备配置仍为 STOPPED。

官方环境定义：[Binance USDⓈ-M General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)。本次所有认证请求均为 GET，无订单、撤单、划转或账户设置写入。

## 企业微信链路

使用原生 `freqtrade.rpc.webhook.Webhook` 模板接口，覆盖开仓/加仓委托、成交、撤单，平仓/减仓委托、成交、撤单，以及启动、状态、警告、异常、局部/全局保护、清算预警，共 13 类。清算预警是否产生取决于当前引擎配置与状态，本次没有触发实仓预警。事件包含交易编号、币种、方向、杠杆、金额、仓位调整标志，退出事件使用引擎当时提供的盈亏值；委托通知不表示已成交。

本地转发器是本地扩展，监听 `127.0.0.1:18789`，经私有随机路径认证，将已接收事件先写入 SQLite，再发往既有企业微信机器人。只有业务返回整数 `errcode=0` 才记录成功；HTTP 200 本身不够。失败指数退避，最多 8 次，事件超过 24 小时标为失败，记录保留；每次发送至少间隔 4 秒。

已安装并启动的唯一新服务为 `io.freqtrade.notifications`（通知服务，不含交易权限或交易密钥）。验收通过原生 Webhook 发送一条明确标注“实盘未启动”的测试，企业微信首次返回 `errcode=0`，见 `receipt.json`。这证明服务端接受，不表示用户已阅读，也不是实际成交通知的验收。

持久化保证从本地 HTTP 接收并入库开始；入库前仍使用原生内存 RPC 队列。崩溃发生在企业微信接受与本地确认之间时可能重复。超出重试上限的事件不会无限重发。主机休眠、断网或代理退出时，同机转发服务不能保证及时通知；没有独立外部失联监控。

健康检查：`http://127.0.0.1:18789/health`。私有 `notification.sqlite` 保存 pending/sent/dead 和尝试回执，不记录机器人 URL。卸载通知服务可使用 `launchctl bootout gui/$(id -u)/io.freqtrade.notifications`，再按需要保留或删除对应 LaunchAgent；此操作不涉及交易服务。

## 复核与测试

在仓库根目录执行下列命令只进行账户 GET 或离线准备，均不启动交易：

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m deploy.live_prepare_20261109.preflight
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m deploy.live_prepare_20261109.prepare
.venv/bin/python -m pytest deploy/live_prepare_20261109/test_notifications.py -q
.venv/bin/ruff check deploy/live_prepare_20261109
```

测试验证企业微信业务错误、重启重试、超时错误脱敏、重试上限、发送间隔、相同文本事件保留、UTF-8 长度及按原生 RPC 类型限定字段的 13 类模板渲染。测试不发送外部消息。

官方接口参考：[Freqtrade Webhook](https://www.freqtrade.io/en/stable/webhook-config/)、[Binance 合约账户](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)、[Binance 合约带单](https://developers.binance.com/en/docs/catalog/advanced-trading-copy-trading/api/rest-api/future-copy-trading)。实际兼容性判断以此仓库固定实现及本次带时间戳的响应为准。
