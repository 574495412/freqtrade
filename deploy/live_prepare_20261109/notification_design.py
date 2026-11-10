"""WeCom presentation adapted from CZ-Agent; native Freqtrade events remain authoritative.

Local extension, not an upstream Freqtrade notifier. Only presentation is ported:
no CZ account, execution, strategy, risk defaults or credential loading is imported.
"""

# ruff: noqa: RUF001 -- Chinese user-facing notifications.

import re


VERSION = "crypto-notifications-v2"
LABELS = {"spot": "现货实盘", "futures": "永续实盘"}


def redact(text):
    """Never forward signed URLs, webhook tokens or credential fields from errors."""
    text = re.sub(r"https?://[^\s<>\"']+", "[地址已隐藏]", text)
    return re.sub(
        r"(?i)(signature|api[_-]?key|api[_-]?secret|secret|token|password|listenkey)"
        r"([\s\"':=]+)[^\s,\"'}&]+",
        r"\1\2[已隐藏]",
        text,
    )


def native_card(content):
    """Reformat already-rendered native text without inventing unavailable fields.

    Running workers keep their original Webhook templates. Unknown text passes
    through, preserving paper-selector and protection messages.
    """
    content = redact(content)
    match = re.match(r"\[NFI X8｜(现货实盘|永续实盘)\] (.*)", content, re.DOTALL)
    if not match:
        return {"msgtype": "text", "text": {"content": content}}
    label, body = match.groups()
    first, _, rest = body.partition("\n")
    if first.startswith("程序启动："):
        title = f"🚀 NFI X8 · {label} · 启动信息"
        # Upstream describes dynamic stake as unlimited; the live allocator caps it.
        body = body.removeprefix("程序启动：").replace(
            "unlimited USDT", "动态分配（实际金额受部署资金限制）"
        )
    elif first.startswith("运行状态："):
        title = f"📡 NFI X8 · {label} · 状态变化"
        body = body.replace("运行状态：running", "进程状态：运行中")
        body = body.replace("运行状态：process died", "进程状态：已退出，需检查")
    elif any(
        first.startswith(x)
        for x in ("运行警告", "运行异常", "交易保护", "全局交易保护", "保证金风险")
    ):
        return {"msgtype": "text", "text": {"content": f"⚠️ NFI X8 · {label}\n{body}"}}
    elif "委托" in first or "成交" in first:
        adjusting = "仓位调整=True" in rest or "仓位调整=是" in rest
        entering = first.startswith("开仓／加仓")
        action = (
            ("加仓" if adjusting else "开仓") if entering else ("减仓" if adjusting else "平仓")
        )
        # "尚未成交" occurs in submission text; only the native fill headline
        # ends with 成交. Never promote an order acknowledgement to a fill.
        suffix = (
            "委托取消"
            if "取消" in first
            else "成交"
            if first.endswith("成交")
            else "委托（待成交）"
        )
        icon = "✅" if suffix == "成交" else "↩️" if "取消" in suffix else "📝"
        title = f"{icon} NFI X8 · {label} · {action}{suffix}"
        body = rest.replace("仓位调整=True", "仓位调整=是").replace("仓位调整=False", "仓位调整=否")
        body = body.replace("｜Long｜", "｜做多｜").replace("｜Short｜", "｜做空｜")
        body = body.replace("\n资金 ", "\n交易资金（引擎） ")
        if not entering:
            body = body.replace(
                "引擎盈亏", "引擎已实现盈亏" if suffix == "成交" else "引擎预计盈亏"
            )
        body += "\n\n> 来源：Freqtrade 原生事件；成交与委托分开通知。"
    else:
        title = f"📡 NFI X8 · {label}"
    return {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n\n{body.strip()}"}}


def runtime_card(snapshots, title="📊 加密实盘 · 运行与成交核对"):
    sections = []
    for snapshot in snapshots:
        label = LABELS[snapshot["market"]]
        faults = snapshot["faults"]
        status = "⚠️ 存在异常" if faults else "✅ 进程与近期日志检查通过"
        lines = [f"**{label}｜{status}**"]
        if snapshot.get("contract"):
            c = snapshot["contract"]
            capital = "现货本金" if snapshot["market"] == "spot" else "合约保证金"
            lines += [
                f"> {capital}上限 {c['ceiling']:g}U｜交易预算 {c['budget']:g}U",
                f"> 配置币池 {c['pairs']} 币｜最多 {c['slots']} 仓｜{c['leverage']}×",
            ]
        heartbeat = snapshot.get("heartbeat_age")
        lines.append(
            f"> 心跳：{int(heartbeat)}秒前" if heartbeat is not None else "> 心跳：无法确认"
        )
        ledger = snapshot.get("ledger")
        if ledger is not None:
            lines.append(
                f"> 本地订单 {ledger['orders']}｜有成交订单 {ledger['filled_orders']}"
                f"｜持仓 {ledger['open_trades']}｜追加入场 {ledger['additions']}次"
            )
            if ledger["closed_trades"]:
                lines.append(
                    f"> 已平仓 {ledger['closed_trades']}笔｜引擎净盈亏 {ledger['closed_pnl']:+.4f}U"
                    f"｜胜率 {ledger['wins'] / ledger['closed_trades']:.1%}"
                )
            else:
                lines.append("> 尚无已平仓交易，胜率暂不可计算")
            if ledger["positions"]:
                for position in ledger["positions"][:6]:
                    lines.append(
                        f"> {position['pair']}｜{'空' if position['short'] else '多'}"
                        f"｜保证金/本金 {position['stake']:.2f}U"
                    )
                if len(ledger["positions"]) > 6:
                    lines.append(f"> 另有 {len(ledger['positions']) - 6} 笔持仓，详见账本")
            if ledger["orders"] == 0:
                lines.append("> 当前没有订单；尚无自然成交可做交易所逐笔验收")
        lines += [f"> ⚠️ {message}" for message in faults.values()]
        if not faults:
            lines.append("> 近期未见所监控的日志异常；不代表已完成成交验收")
        sections.append("\n".join(lines))
    body = "\n\n".join(sections)
    body += "\n\n> 统计来自本策略原生账本；盈亏未包含未平仓浮盈亏，不等于账户总收益。"
    return {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n\n{body}"}}
