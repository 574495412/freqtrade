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

from deploy.live_prepare_20261109.notification_design import VERSION, health_message
from deploy.live_prepare_20261109.notification_orders import (
    publish_order_alerts,
    publish_orders,
    read_activity,
)
from deploy.live_prepare_20261109.notification_reasons import decision_from_log
from deploy.live_prepare_20261109.preflight import ROOT


LIVE = ROOT / "user_data/deployment/mainnet-100-20261109"
TZ = ZoneInfo("Asia/Shanghai")
WINDOW = 300
HEARTBEAT_MAX_AGE = 300
RECOVERY_CONFIRMATION = 60
ORDER_FAILURE = re.compile(
    r" - freqtrade\.freqtradebot - WARNING - Unable to "
    r"(?:(?P<entry>create trade for)|exit trade) "
    r"(?P<pair>[A-Z0-9]+/[A-Z0-9]+(?::[A-Z0-9]+)?):\s*"
    r"(?:Could not (?:place|create) |(?P<funds>Insufficient funds to create) )"
)
EXCHANGE_CODE = re.compile(r"""["']code["']\s*:\s*(-\d+)""")


def ledger_snapshot(path, *, since=None):
    # Explicit read-only URI: a missing DB is an error, never a new empty account.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        return {
            "open_trades": conn.execute("SELECT count(*) FROM trades WHERE is_open").fetchone()[0],
            "order_activity": read_activity(conn, since),
        }


def log_snapshot(path, now, pid, started, prior):  # noqa: C901 -- Explicit log fault categories.
    """Read a bounded tail, preserving observed heartbeat across verbose log bursts."""
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(max(0, size - 2 * 1024 * 1024))
        raw = file.read().decode(errors="replace")
    same_process = prior.get("pid") == pid and prior.get("started") == started
    heartbeat = prior.get("heartbeat") if same_process else None
    last_state = prior.get("native_state") if same_process else None
    faults = {}
    banned_until = None
    rejections = {}
    decisions = []
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
        decision = decision_from_log(line, stamp)
        if decision is not None:
            decisions.append(decision)
        order_failure = ORDER_FAILURE.search(line)
        if order_failure:
            pair = order_failure["pair"]
            side = "entry" if order_failure["entry"] else "exit"
            match = EXCHANGE_CODE.search(line)
            code = match[1] if match else "insufficient_funds" if order_failure["funds"] else None
            key = f"{pair}:{side}:{code}"
            rejection = rejections.setdefault(
                key, {"pair": pair, "side": side, "code": code, "first": stamp}
            )
            rejection["last"] = stamp
            continue
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
        elif (
            (" - WARNING - " in line or " - ERROR - " in line)
            and ("candle" in lower or "ohlcv" in lower)
            and not re.search(r" - WARNING - Using [1-9]\d* calls to get OHLCV\. ", line)
        ):
            # Upstream validates startup history with this performance warning;
            # multiple pages are expected and do not establish a data failure.
            faults["candles"] = "近期K线请求失败或超时，不能确认全部币种行情新鲜"
        elif "could not call webhook" in lower or "problem calling webhook" in lower:
            faults["native_webhook"] = "原生风控事件回调失败，请检查本地通知入口"
        elif " - ERROR - " in line or " - CRITICAL - " in line:
            faults["engine_error"] = "引擎近期记录错误，请检查本地运行日志"
    if "rate_limit" in faults:
        faults.pop("candles", None)
    if (prior.get("banned_until") or 0) > now:
        banned_until = max(banned_until or 0, prior["banned_until"])
    if banned_until and banned_until > now:
        until = datetime.fromtimestamp(banned_until, TZ).strftime("%m-%d %H:%M:%S")
        faults["rate_limit"] = f"行情请求遭418限流，最近日志报告限制至 {until}；尚未确认恢复"
    return {
        "heartbeat": heartbeat,
        "native_state": last_state,
        "faults": faults,
        "banned_until": banned_until,
        "rejections": rejections,
        "decisions": decisions,
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


def worker_market(command):
    if "--worker" in command:
        return command[command.index("--worker") + 1]
    if command[-3:-1] == ["-m", "deploy.runtime_recovery_20261111.service"]:
        return command[-1]
    raise ValueError("unrecognized worker entrypoint")


def guard_fault(path, now):
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            (until,) = conn.execute("SELECT cooldown FROM budget WHERE id=1").fetchone()
        if until > now:
            end = datetime.fromtimestamp(until, TZ).strftime("%m-%d %H:%M:%S")
            return {"shared_cooldown": f"交易所请求暂停至 {end}，请查看共享限流状态"}
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return {"shared_cooldown_unknown": "共享限流状态暂不可读"}
    return {}


def observe(market, now, prior=None, root=LIVE):
    prior = prior or {}
    folder = root / market
    result = {"market": market, "observed": now, "faults": {}, "ledger": None}
    try:
        config = json.loads((folder / "config.json").read_text())
        if config.get("dry_run") is not False or config.get("trading_mode") != market:
            raise ValueError("wrong contract")
        _, faults = pair_count(config, now)
        result["faults"].update(faults)
    except (OSError, ValueError, KeyError, TypeError):
        result["faults"]["config"] = "无法读取或确认当前实盘配置"
    try:
        pid = int(json.loads((folder / "worker.pid").read_text())["pid"])
        process = psutil.Process(pid)
        command = process.cmdline()
        if worker_market(command) != market or process.cwd() != str(ROOT):
            raise ValueError("wrong worker")
        result["pid"] = pid
        started = result["started"] = process.create_time()
        logs = log_snapshot(folder / "freqtrade.log", now, pid, started, prior)
        result.update({key: value for key, value in logs.items() if key != "faults"})
        result["faults"].update(logs["faults"])
        heartbeat = result.get("heartbeat")
        age = now - heartbeat if heartbeat is not None else None
        result["heartbeat_age"] = age
        if age is None:
            if now - started >= HEARTBEAT_MAX_AGE:
                result["faults"]["heartbeat"] = "5 分钟内未见心跳，请检查策略循环和行情"
        elif age >= HEARTBEAT_MAX_AGE:
            result["faults"]["heartbeat"] = "心跳中断超过 5 分钟，请检查策略循环和行情"
        elif result.get("native_state") != "RUNNING":
            result["faults"]["native_state"] = "引擎心跳状态不是 RUNNING"
    except (OSError, ValueError, KeyError, IndexError, TypeError, psutil.Error):
        result["faults"]["process"] = "交易进程不存在或身份不匹配，需检查"
    try:
        result["ledger"] = ledger_snapshot(
            folder / "trades.sqlite", since=prior.get("order_scan_at")
        )
    except (OSError, ValueError, TypeError, sqlite3.Error):
        result["faults"]["ledger"] = "原生交易账本暂不可读，成交和持仓数量未知"
    result["faults"].update(guard_fault(root.parent / "binance-request-guard.sqlite", now))
    return result


def health_transition(current, prior, now):
    incident = prior.get("incident")
    faults = current["faults"]
    if faults:
        previous = incident["faults"] if incident else {}
        if incident and faults.keys() <= previous.keys():
            return {"faults": previous, "clear_since": None, "clear_heartbeat": None}, None
        ledger = current.get("ledger")
        positions = ledger["open_trades"] if ledger is not None else None
        return {
            "faults": previous | faults,
            "clear_since": None,
            "clear_heartbeat": None,
        }, health_message(current["market"], list(faults.values()), positions, True)
    if incident is None:
        return None, None
    heartbeat = current.get("heartbeat")
    if current.get("native_state") != "RUNNING" or heartbeat is None or now - heartbeat > 120:
        return {**incident, "clear_since": None, "clear_heartbeat": None}, None
    since = incident["clear_since"]
    if since is None:
        return {**incident, "clear_since": now, "clear_heartbeat": heartbeat}, None
    if now - since < RECOVERY_CONFIRMATION or heartbeat <= incident["clear_heartbeat"]:
        return incident, None
    return None, health_message(current["market"], list(incident["faults"].values()), None, False)


class RuntimeMonitor:
    def __init__(self, outbox, observer=observe):
        self.outbox = outbox
        self.observer = observer
        self.last_check = None
        self.error = None
        self.markets = {}

    def poll(self, now):
        with self.outbox.connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM observations WHERE name=?", (VERSION,)
            ).fetchone()
        previous = json.loads(row[0]) if row else {}
        current = {m: self.observer(m, now, previous.get(m)) for m in ("spot", "futures")}
        # Reads of the trading DB and logs stay outside the outbox write lock.
        # Only this observer writes the cursor. Event and cursor commit together.
        with self.outbox.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for market, snapshot in current.items():
                prior = previous.get(market, {})
                snapshot["incident"], alert = health_transition(snapshot, prior, now)
                if alert is not None:
                    self.outbox.enqueue(alert, now=now, connection=conn)
                ledger = snapshot.get("ledger")
                orders = ledger.pop("order_activity", None) if ledger is not None else None
                since = prior.get("order_scan_at")
                snapshot["order_scan_at"] = now if orders is not None else since
                if orders is not None:
                    publish_orders(
                        self.outbox,
                        conn,
                        market,
                        orders,
                        now,
                        seed=since is None,
                        decisions=snapshot.pop("decisions", ()),
                    )
                    snapshot["order_alerts"] = publish_order_alerts(
                        self.outbox, conn, snapshot, prior, orders, now
                    )
                else:
                    snapshot["order_alerts"] = prior.get("order_alerts", [])
            conn.execute(
                """INSERT INTO observations(name,snapshot,updated) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE
                   SET snapshot=excluded.snapshot,updated=excluded.updated""",
                (VERSION, json.dumps(current), now),
            )
        self.last_check = now
        self.error = None
        self.markets = {
            market: {
                "state": snapshot.get("native_state"),
                "last_heartbeat": snapshot.get("heartbeat"),
                "faults": sorted(snapshot["faults"]),
            }
            for market, snapshot in current.items()
        }
        return current
