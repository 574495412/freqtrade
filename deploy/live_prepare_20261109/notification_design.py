"""WeCom presentation adapted from CZ-Agent; native Freqtrade events remain authoritative.

Local extension, not an upstream Freqtrade notifier. Only presentation is ported:
no CZ account, execution, strategy, risk defaults or credential loading is imported.
"""

# ruff: noqa: RUF001 -- Chinese user-facing notifications.

import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from deploy.live_prepare_20261109.notification_reasons import reason_lines


VERSION = "crypto-notifications-v3"
TZ = ZoneInfo("Asia/Shanghai")
LABELS = {"spot": "现货", "futures": "合约"}


def redact(text):
    """Never forward signed URLs, webhook tokens or credential fields from errors."""
    text = re.sub(r"https?://[^\s<>\"']+", "[地址已隐藏]", text)
    return re.sub(
        r"(?i)(signature|api[_-]?key|api[_-]?secret|secret|token|password|listenkey)"
        r"([\s\"':=]+)[^\s,\"'}&]+",
        r"\1\2[已隐藏]",
        text,
    )


LIVE_PREFIX = re.compile(r"\[NFI X8｜(现货实盘|永续实盘)\] (.*)", re.DOTALL)
QUIET_HEADINGS = (
    "开仓／加仓",
    "平仓／减仓",
    "程序启动：",
    "运行状态：",
    "日更选币更新成功：",
    "日更选币数据更新失败：",
)


def text_message(content):
    return {"msgtype": "text", "text": {"content": content}}


def native_card(content):
    """Keep the live RPC ingress compatible while the ledger owns order updates."""
    match = LIVE_PREFIX.fullmatch(content)
    if not match:
        return text_message(content)
    market, body = match.groups()
    if body.startswith(QUIET_HEADINGS):
        return None
    label = "现货" if market == "现货实盘" else "合约"
    first, _, rest = body.partition("\n")
    if first.startswith("运行异常："):
        return text_message(f"{label}｜引擎运行异常\n查看服务日志与持仓管理状态")
    if first.startswith("运行警告："):
        open_trades = re.search(r"(\d+) open trades active", first)
        if open_trades:
            return text_message(f"{label}｜引擎停止时仍有 {open_trades[1]} 笔持仓\n核对持仓与挂单")
        return text_message(f"{label}｜{first[:180]}")
    # Keep native protections and liquidation warnings, which the order ledger
    # cannot report. The relay redacts and bounds the final payload.
    return text_message(f"{label}｜{first}\n{rest.strip()}".rstrip())


def clock(stamp):
    return datetime.fromtimestamp(stamp, TZ).strftime("%m-%d %H:%M:%S")


def display_pair(pair):
    return pair.split(":", 1)[0]


def number(amount):
    return format(Decimal(str(amount)).normalize(), "f")


def order_heading(market, order, action):
    pair = order["pair"]
    side = "空" if order["is_short"] else "多"
    if market == "futures":
        side += f" {order['leverage']:g}×"
    return f"{LABELS[market]}｜{action}｜{display_pair(pair)} {side}"


def submission_message(market, order, evidence):
    action = order["action"] if order["entry"] else "退出"
    lines = [order_heading(market, order, f"{action}委托已提交 · 待成交")]
    qty = order["requested_amount"]
    limit = order["limit_price"]
    if qty:
        asset = order["pair"].split("/")[0]
        lines.append(f"委托 {number(qty)} {asset}" + (f" @ {number(limit)}" if limit else ""))
    lines.extend(reason_lines(order["order_tag"], evidence))
    stamp = order["created_at"] or order["updated_at"]
    lines.append(f"{clock(stamp)}｜交易 #{order['trade_id']}")
    return text_message("\n".join(lines))


def fill_message(market, order, evidence=None):
    pair = order["pair"]
    action = order["action"]
    if order["is_open"]:
        action += "部分成交"
    elif order["status"] != "closed":
        action += "部分成交，余量未成交"
    else:
        action += "成交"
    lines = [
        order_heading(market, order, action),
        f"本单累计 {number(order['filled'])} {pair.split('/')[0]}"
        + (f" @ {number(order['average'])}" if order["average"] else ""),
    ]
    pnl = order["closed_pnl"]
    if pnl is not None:
        currency = pair.split("/")[1].split(":")[0]
        lines.append(f"本笔交易累计已实现 {pnl:+.2f} {currency}（账本）")
    lines.extend(reason_lines(order["order_tag"], evidence))
    stamp = order["filled_at"]
    event_time = clock(stamp) if stamp is not None else None
    label = f"{event_time} 成交" if event_time else f"{clock(order['updated_at'])} 账本更新"
    lines.append(f"{label}｜交易 #{order['trade_id']}")
    return text_message("\n".join(lines))


def health_message(market, details, positions, active):
    label = LABELS[market]
    if not active:
        return text_message(f"{label}｜监控恢复\n此前：{'、'.join(details)}")
    exposure = f"｜账本持仓 {positions}" if positions is not None else ""
    return text_message(f"{label}｜交易异常{exposure}\n" + "\n".join(details))
