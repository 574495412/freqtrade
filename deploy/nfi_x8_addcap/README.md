# NFI X8 AddCap 独立模拟盘

现货 36 对和永续 40 对分别运行 `NFIX8AddCapPaper`，每边初始资金 1,000 USDT，从独立空仓账本开始。永续沿用逐仓 1×，币池、手续费、原生信号及退出参数从已验证的原版配置继承。原版和 AddCap 的起始时刻及持仓不同，前向收益应从共同观察边界分段评估。

只对非纯 Top Coins 多头的仓位检查正向追加，包括空头及混合标签。减仓、退出和纯 Top Coins 多头的加仓继续使用原策略。每个受限仓位最多有两笔成交追加。每次提交前用原生订单账本计算首笔已成交金额与已成交追加金额，拟追加额超过余额时整笔拒绝。待成交入场单会阻止再提交，撤销但有成交的订单会消耗一个追加名额，零成交撤单不消耗名额。减仓不恢复额度。实际执行价由 Freqtrade 的模拟撮合决定，提交额度不构成实际滑点、止损或最大亏损保证。

启动器验证固定引擎、原版 X8 和候选文件摘要。每套独立配置、SQLite、策略缓存和实例锁位于 `user_data/deployment/nfi-x8-addcap/{spot,futures}/`。启动时使用 `--dry-run` 和精简环境，配置没有账户凭据或通知集成。常驻标签为 `io.freqtrade.paper.x8-addcap.spot` 和 `io.freqtrade.paper.x8-addcap.futures`。

验证命令在临时目录加载策略后使用官方配置校验，无需连接交易所或读取运行账本。

```sh
.venv-paper/bin/python -B deploy/nfi_x8_addcap/launch.py spot --verify
.venv-paper/bin/python -B deploy/nfi_x8_addcap/launch.py futures --verify
.venv/bin/python -B -m pytest -q deploy/nfi_x8_addcap/test_addcap.py
```

本地查看进程或卸载候选服务时，以相应市场替换示例中的 `spot`。服务文件在用户 LaunchAgents 下，若需取消以后登录时自动启动，还需移除对应的候选文件。

```sh
launchctl print "gui/$(id -u)/io.freqtrade.paper.x8-addcap.spot"
launchctl bootout "gui/$(id -u)/io.freqtrade.paper.x8-addcap.spot"
```

本次验收范围为配置、原生账本、模拟状态和规则回归。自然成交、真实保护单和收益改善均需独立证据。[Freqtrade 模拟配置](https://www.freqtrade.io/en/stable/configuration/#using-dry-run-mode) · [仓位调整回调](https://www.freqtrade.io/en/stable/strategy-callbacks/#adjust-trade-position)
