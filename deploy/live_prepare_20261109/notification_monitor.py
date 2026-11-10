"""Read-only local health/ledger observer. No exchange clients or trading imports.

Changes only are enqueued, with observer state and message in one SQLite
transaction. Stable warnings do not repeat on each poll or relay restart.
"""

# ruff: noqa: RUF001 -- Chinese user-facing notifications.

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import psutil

from deploy.live_prepare_20261109.notification_design import VERSION, runtime_card
from deploy.live_prepare_20261109.preflight import ROOT


LIVE = ROOT / "user_data/deployment/mainnet-100-20261109"
TZ = ZoneInfo("Asia/Shanghai")
WINDOW = 300
HEARTBEAT_MAX_AGE = 180


def ledger_snapshot(path):
    # Explicit read-only URI: a missing DB is an error, never a new empty account.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        totals = dict(
            conn.execute("""SELECT count(*) AS trades,
            coalesce(sum(is_open),0) AS open_trades,
            coalesce(sum(NOT is_open),0) AS closed_trades,
            coalesce(sum(CASE WHEN NOT is_open AND close_profit_abs>0 THEN 1 ELSE 0 END),0) AS wins,
            coalesce(sum(CASE WHEN NOT is_open THEN close_profit_abs ELSE 0 END),0) AS closed_pnl
            FROM trades""").fetchone()
        )
        totals.update(
            dict(
                conn.execute("""SELECT count(*) AS orders,
            coalesce(sum(filled>0),0) AS filled_orders,
            coalesce(sum(ft_is_open),0) AS open_orders FROM orders""").fetchone()
            )
        )
        totals["additions"] = conn.execute("""SELECT coalesce(sum(max(n-1,0)),0) FROM (
            SELECT count(*) AS n FROM orders o JOIN trades t ON t.id=o.ft_trade_id
            WHERE o.status='closed' AND o.filled>0
            AND o.ft_order_side=CASE WHEN t.is_short THEN 'sell' ELSE 'buy' END
            GROUP BY t.id)""").fetchone()[0]
        totals["positions"] = [
            {"pair": r["pair"], "short": bool(r["is_short"]), "stake": r["stake_amount"]}
            for r in conn.execute(
                "SELECT pair,is_short,stake_amount FROM trades WHERE is_open ORDER BY id"
            )
        ]
        return totals


def log_snapshot(path, now, pid, started, prior):  # noqa: C901 -- Explicit log fault categories.
    """Read a bounded tail, preserving observed heartbeat across verbose log bursts."""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - 2 * 1024 * 1024))
        raw = file.read().decode(errors="replace")
    heartbeat = prior.get("heartbeat") if prior.get("pid") == pid else None
    last_state = prior.get("native_state") if prior.get("pid") == pid else None
    faults = {}
    banned_until = None
    for line in raw.splitlines():
        try:
            stamp = (
                datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=TZ).timestamp()
            )
        except ValueError:
            continue
        if stamp < started - 2 or stamp > now + 10:
            continue
        if f"Bot heartbeat. PID={pid}," in line and (heartbeat is None or stamp > heartbeat):
            heartbeat = stamp
            last_state = "RUNNING" if "state='RUNNING'" in line else "NOT_RUNNING"
        if not 0 <= now - stamp <= WINDOW:
            continue
        lower = line.lower()
        if "418" in line and ("banned" in lower or "teapot" in lower):
            faults["rate_limit"] = "行情请求遭 Binance 418 限流，当前不能确认行情可用"
            # Only the expiry timestamp is extracted; never forward signed URLs/IPs.
            match = re.search(r"banned until (\d{13})", line)
            if match:
                banned_until = max(banned_until or 0, int(match[1]) / 1000)
        elif "429" in line and ("too many" in lower or "ddos" in lower):
            faults["rate_limit"] = "行情请求遭 Binance 429 限流，需检查行情更新"
        elif "could not load markets" in lower:
            faults["market_load"] = "交易所市场元数据加载失败"
        elif (" - WARNING - " in line or " - ERROR - " in line) and (
            "candle" in lower or "ohlcv" in lower
        ):
            faults["candles"] = "近期K线请求失败或超时，不能确认全部币种行情新鲜"
        elif " - ERROR - " in line or " - CRITICAL - " in line:
            faults["engine_error"] = "引擎近期记录错误，需检查本地运行日志"
        elif "could not call webhook" in lower or "problem calling webhook" in lower:
            faults["native_webhook"] = "原生交易事件推送失败，可能缺少委托或成交通知"
    if "rate_limit" in faults:
        faults.pop("candles", None)
    if prior.get("pid") == pid and (prior.get("banned_until") or 0) > now:
        banned_until = max(banned_until or 0, prior["banned_until"])
    if banned_until and banned_until > now:
        until = datetime.fromtimestamp(banned_until, TZ).strftime("%m-%d %H:%M:%S")
        faults["rate_limit"] = f"行情请求遭418限流，最近日志报告限制至 {until}；尚未确认恢复"
    return {
        "heartbeat": heartbeat,
        "native_state": last_state,
        "faults": faults,
        "banned_until": banned_until,
    }


def pair_count(config, now):
    """Read the native local RemotePairList snapshot, never fetch a remote URL."""
    pairs = config["exchange"]["pair_whitelist"]
    faults = {}
    for rule in config.get("pairlists", []):
        if rule.get("method") != "RemotePairList":
            continue
        url = urlparse(rule["pairlist_url"])
        path = Path(unquote(url.path)).resolve()
        if (
            url.scheme != "file"
            or url.netloc
            or not path.is_relative_to(ROOT / "user_data/deployment")
        ):
            raise ValueError("Unsupported pair source")
        snapshot = json.loads(path.read_text())
        pairs = snapshot["pairs"][: rule["number_assets"]]
        expires = snapshot.get("valid_until_utc")
        if not expires or datetime.fromisoformat(expires).timestamp() <= now:
            faults["pairlist_expired"] = "本地选币快照已过期或缺失有效期，需检查新开仓准入"
    if not isinstance(pairs, list) or not all(isinstance(pair, str) for pair in pairs):
        raise ValueError("Invalid pair snapshot")
    return len(pairs), faults


def observe(market, now, prior=None, root=LIVE):
    prior = prior or {}
    folder = root / market
    result = {"market": market, "observed": now, "faults": {}, "ledger": None}
    try:
        config = json.loads((folder / "config.json").read_text())
        if config.get("dry_run") is not False or config.get("trading_mode") != market:
            raise ValueError("wrong contract")
        leverage = 1 if market == "spot" else config["nfi_parameters"]["futures_mode_leverage"]
        pairs, faults = pair_count(config, now)
        result["faults"].update(faults)
        result["contract"] = {
            "ceiling": config["capital_ceiling_usdt"],
            "budget": config["available_capital"],
            "pairs": pairs,
            "slots": config["max_open_trades"],
            "leverage": f"{leverage:g}",
        }
    except (OSError, ValueError, KeyError, TypeError):
        result["faults"]["config"] = "无法读取或确认当前实盘配置"
    try:
        pid = int(json.loads((folder / "worker.pid").read_text())["pid"])
        process = psutil.Process(pid)
        command = process.cmdline()
        index = command.index("--worker")
        if command[index + 1] != market or process.cwd() != str(ROOT):
            raise ValueError("wrong worker")
        result["pid"] = pid
        logs = log_snapshot(folder / "freqtrade.log", now, pid, process.create_time(), prior)
        result.update({key: value for key, value in logs.items() if key != "faults"})
        result["faults"].update(logs["faults"])
        heartbeat = result.get("heartbeat")
        age = now - heartbeat if heartbeat is not None else None
        result["heartbeat_age"] = age
        if age is None or age > HEARTBEAT_MAX_AGE:
            result["faults"]["heartbeat"] = "心跳缺失或超过3分钟，不能确认策略循环正常"
        elif result.get("native_state") != "RUNNING":
            result["faults"]["native_state"] = "引擎心跳状态不是 RUNNING"
    except (OSError, ValueError, KeyError, IndexError, TypeError, psutil.Error):
        result["faults"]["process"] = "交易进程不存在或身份不匹配，需检查"
    try:
        result["ledger"] = ledger_snapshot(folder / "trades.sqlite")
    except (OSError, ValueError, sqlite3.Error):
        result["faults"]["ledger"] = "原生交易账本暂不可读，成交和持仓数量未知"
    return result


def fingerprint(snapshot):
    ledger = snapshot.get("ledger")
    return (
        snapshot.get("pid"),
        tuple(sorted(snapshot["faults"])),
        tuple(
            ledger[k]
            for k in ("orders", "filled_orders", "open_trades", "closed_trades", "additions")
        )
        if ledger
        else None,
    )


class RuntimeMonitor:
    def __init__(self, outbox, observer=observe):
        self.outbox = outbox
        self.observer = observer
        self.last_check = None
        self.error = None

    def poll(self, now):
        # Transaction covers both durable observer cursor and outgoing message.
        with self.outbox.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT snapshot FROM observations WHERE name=?", (VERSION,)
            ).fetchone()
            previous = json.loads(row[0]) if row else {}
            current = {m: self.observer(m, now, previous.get(m)) for m in ("spot", "futures")}
            changes = [
                value
                for market, value in current.items()
                if market not in previous or fingerprint(value) != fingerprint(previous[market])
            ]
            if changes:
                title = (
                    "📊 加密实盘 · 通知升级与运行核对"
                    if not previous
                    else "📡 加密实盘 · 状态或账本变化"
                )
                self.outbox.enqueue(runtime_card(changes, title), now=now, connection=conn)
            conn.execute(
                """INSERT INTO observations(name,snapshot,updated) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE
                   SET snapshot=excluded.snapshot,updated=excluded.updated""",
                (VERSION, json.dumps(current), now),
            )
        self.last_check = now
        self.error = None
        return current
