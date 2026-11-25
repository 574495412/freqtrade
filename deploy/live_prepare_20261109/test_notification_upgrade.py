"""Native presentation, read-only diagnostics and durable state-change delivery."""

# ruff: noqa: S101, RUF001 -- Pytest and Chinese notification fixtures.

import json
import sqlite3
from datetime import datetime

import pytest

from deploy.live_prepare_20261109.notification_design import native_card, redact
from deploy.live_prepare_20261109.notification_monitor import (
    ROOT,
    TZ,
    RuntimeMonitor,
    ledger_snapshot,
    log_snapshot,
    observe,
    pair_count,
)
from deploy.live_prepare_20261109.notify_relay import Outbox
from deploy.live_prepare_20261109.prepare import webhook_config
from freqtrade.enums import RPCMessageType
from freqtrade.rpc.webhook import Webhook


def messages(box):
    with box.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM messages ORDER BY id")]


@pytest.mark.parametrize(
    "event",
    [
        "entry",
        "entry_fill",
        "entry_cancel",
        "exit",
        "exit_fill",
        "exit_cancel",
        "startup",
        "status",
    ],
)
def test_deployed_native_template_is_accepted_quietly(tmp_path, event):
    box = Outbox(tmp_path / "outbox.sqlite")
    webhook = Webhook(None, {"webhook": webhook_config("futures", "http://127.0.0.1/unused")})
    webhook._send_msg = box.enqueue
    webhook.send_msg(
        {
            "type": RPCMessageType(event),
            "trade_id": 7,
            "pair": "ADA/USDT:USDT",
            "direction": "Short",
            "leverage": 3.0,
            "amount": 20,
            "order_rate": 0.4,
            "open_rate": 0.41,
            "stake_amount": 8,
            "stake_currency": "USDT",
            "sub_trade": True,
            "reason": "timeout",
            "profit_amount": -0.2,
            "profit_ratio": -0.025,
            "exit_reason": "stop_loss",
            "status": "running",
        }
    )
    assert not messages(box)


def test_risk_messages_survive_and_selector_noise_is_silent(tmp_path):
    box = Outbox(tmp_path / "outbox.sqlite")
    for headline in ("日更选币更新成功：80币", "日更选币数据更新失败：RuntimeError"):
        assert native_card(f"[NFI X8｜永续实盘] {headline}") is None
    box.enqueue(
        {
            "msgtype": "text",
            "text": {"content": "[NFI X8｜永续实盘] 保证金风险预警：ADA/USDT:USDT"},
        }
    )
    assert "合约｜保证金风险预警" in messages(box)[0]["payload"]


def test_redaction_before_durable_storage_and_unknown_legacy_text(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue(
        {
            "msgtype": "text",
            "text": {
                "content": "[旧模拟通知] signature=secret123 https://qyapi.weixin.qq.com/path?key=private"
            },
        }
    )
    payload = messages(box)[0]["payload"]
    assert "secret123" not in payload and "key=private" not in payload
    assert "旧模拟通知" in payload
    assert "abc" not in redact('api_key="abc" token:abc password=abc')


def test_markdown_is_bounded(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue({"msgtype": "markdown", "markdown": {"content": "汉" * 2000}})
    content = json.loads(messages(box)[0]["payload"])["markdown"]["content"]
    assert len(content.encode()) <= 3800


def create_ledger(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE trades(id INTEGER PRIMARY KEY,is_open INTEGER,close_profit_abs REAL,
                pair TEXT,is_short INTEGER,stake_amount REAL,leverage REAL DEFAULT 3,
                enter_tag TEXT,exit_reason TEXT);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, ft_trade_id INTEGER,filled REAL,
                ft_is_open INTEGER,status TEXT,ft_order_side TEXT,order_id TEXT,average REAL,
                order_date TEXT,order_filled_date TEXT,order_update_date TEXT,
                ft_order_tag TEXT,amount REAL,ft_amount REAL,price REAL,order_type TEXT);
            INSERT INTO trades(id,is_open,close_profit_abs,pair,is_short,stake_amount) VALUES
                (1,0,-2,'BTC/USDT',0,10), (2,0,1,'ETH/USDT',1,10), (3,1,NULL,'SOL/USDT',0,5);
            INSERT INTO orders(ft_trade_id,filled,ft_is_open,status,ft_order_side) VALUES
                (1,1,0,'closed','buy'), (1,1,0,'closed','buy'), (1,2,0,'closed','sell'),
                (2,1,0,'closed','sell'), (2,1,0,'closed','buy'), (3,0,1,'open','buy');
            UPDATE orders SET order_id='exchange-' || id, order_date='1970-01-01 00:00:01',
                order_update_date='1970-01-01 00:00:01';
            UPDATE trades SET enter_tag='120' WHERE id=3;
        """)


def test_native_ledger_is_read_only(tmp_path):
    path = tmp_path / "trades.sqlite"
    create_ledger(path)
    before = path.read_bytes()
    ledger = ledger_snapshot(path)
    assert ledger["open_trades"] == 1
    orders = ledger["order_activity"]
    assert [o["action"] for o in orders] == ["开仓", "加仓", "平仓", "开仓", "平仓", "开仓"]
    assert orders[2]["closed_pnl"] == -2 and orders[4]["closed_pnl"] == 1
    assert path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        ledger_snapshot(tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()


def test_418_warning_without_error_log_is_detected_and_expiry_retained(tmp_path):
    now = datetime(2026, 11, 10, 10, 30, tzinfo=TZ).timestamp()
    path = tmp_path / "log"
    expiry = int((now + 1200) * 1000)
    path.write_text(
        "2026-11-10 10:29:59,000 - worker - INFO - Bot heartbeat. PID=42, state='RUNNING'\n"
        f"2026-11-10 10:29:59,001 - exchange - WARNING - binance 418 banned until {expiry}\n"
    )
    result = log_snapshot(path, now, 42, now - 100, {})
    assert result["native_state"] == "RUNNING"
    assert "rate_limit" in result["faults"]
    assert result["heartbeat"] == now - 1
    path.write_text("")  # Huge bursts / rotation must not clear an unexpired restriction.
    again = log_snapshot(
        path, now + 400, 42, now - 100, {"pid": 42, "started": now - 100, **result}
    )
    assert "rate_limit" in again["faults"]
    assert again["heartbeat"] == result["heartbeat"]
    expired = log_snapshot(path, now + 1300, 42, now - 100, {"pid": 42, **again})
    assert "rate_limit" not in expired["faults"]


def test_old_process_errors_are_not_replayed_as_current_faults(tmp_path):
    now = datetime(2026, 11, 10, 10, 30, tzinfo=TZ).timestamp()
    path = tmp_path / "log"
    path.write_text(
        "2026-11-10 10:29:20,000 - exchange - ERROR - Could not load markets.\n"
        "2026-11-10 10:29:50,000 - worker - INFO - Bot heartbeat. PID=43, state='RUNNING'\n"
    )
    result = log_snapshot(path, now, 43, now - 15, {"pid": 42, "heartbeat": now - 1})
    assert not result["faults"] and result["heartbeat"] == now - 10


@pytest.mark.parametrize(
    ("message", "failed"),
    [
        ("Using 2 calls to get OHLCV. This can result in slower operations for the bot.", False),
        ("Could not fetch historical candle (OHLCV) data due to RequestTimeout", True),
    ],
)
def test_startup_pagination_is_not_a_failed_candle_request(tmp_path, message, failed):
    now = datetime(2026, 11, 10, 10, 30, tzinfo=TZ).timestamp()
    path = tmp_path / "log"
    path.write_text(
        "2026-11-10 10:29:59,000 - worker - INFO - Bot heartbeat. PID=42, state='RUNNING'\n"
        f"2026-11-10 10:29:59,001 - exchange - WARNING - {message}\n"
    )
    result = log_snapshot(path, now, 42, now - 100, {})
    assert ("candles" in result["faults"]) is failed
    assert result["native_state"] == "RUNNING"


def test_unreadable_account_is_unknown_not_zero(tmp_path):
    result = observe("spot", 100, root=tmp_path)
    assert result["ledger"] is None
    assert set(result["faults"]) == {"config", "process", "ledger"}


def test_remote_pairlist_is_read_from_native_snapshot_and_expiry_checked(tmp_path, monkeypatch):
    monkeypatch.setattr("deploy.live_prepare_20261109.notification_monitor.ROOT", tmp_path)
    folder = tmp_path / "user_data/deployment"
    folder.mkdir(parents=True)
    path = folder / "pairs.json"
    path.write_text(
        json.dumps(
            {"pairs": ["BTC/USDT", "ADA/USDT"], "valid_until_utc": "2026-11-10T02:00:00+00:00"}
        )
    )
    config = {
        "exchange": {"pair_whitelist": []},
        "pairlists": [
            {"method": "RemotePairList", "number_assets": 80, "pairlist_url": path.as_uri()}
        ],
    }
    valid = datetime(2026, 11, 10, 9, 59, tzinfo=TZ).timestamp()
    assert pair_count(config, valid) == (2, {})
    assert "pairlist_expired" in pair_count(config, valid + 120)[1]
    config["pairlists"][0]["pairlist_url"] = "https://example.org/pairs"
    with pytest.raises(ValueError):
        pair_count(config, valid)


@pytest.mark.parametrize(
    "command",
    [
        ["python", "--worker", "spot"],
        ["python", "-B", "-m", "deploy.runtime_recovery_20261111.service", "spot"],
    ],
)
def test_alive_worker_with_stale_heartbeat_is_not_healthy(tmp_path, monkeypatch, command):
    path = tmp_path / "spot"
    path.mkdir()
    (path / "worker.pid").write_text('{"pid":42}')
    (path / "freqtrade.log").write_text("")
    create_ledger(path / "trades.sqlite")

    class Process:
        def cmdline(self):
            return command

        def cwd(self):
            return str(ROOT)

        def create_time(self):
            return 0

    monkeypatch.setattr(
        "deploy.live_prepare_20261109.notification_monitor.psutil.Process", lambda pid: Process()
    )
    result = observe(
        "spot",
        1000,
        {"pid": 42, "started": 0, "heartbeat": 700, "native_state": "RUNNING"},
        root=tmp_path,
    )
    assert result["heartbeat_age"] == 300 and "heartbeat" in result["faults"]
    assert "process" not in result["faults"]


def snapshot(market, now, fault=False):
    return {
        "market": market,
        "observed": now,
        "pid": 42,
        "heartbeat_age": 1,
        "heartbeat": now - 1,
        "native_state": "RUNNING",
        "faults": {"rate_limit": "行情请求遭 Binance 418 限流"} if fault else {},
        "contract": {"ceiling": 100, "budget": 99, "pairs": 36, "slots": 6, "leverage": "1"},
        "ledger": {
            "orders": 0,
            "filled_orders": 0,
            "open_trades": 0,
            "closed_trades": 0,
            "additions": 0,
            "closed_pnl": 0,
            "wins": 0,
            "positions": [],
        },
    }


def test_observer_incident_quiet_healthy_and_restart_dedup(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    faults = {"futures": False}
    counter = 0

    def observer(market, now, prior):
        result = snapshot(market, now, faults.get(market, False))
        result["ledger"]["orders"] = counter
        result["pid"] += counter
        return result

    monitor = RuntimeMonitor(box, observer)
    monitor.poll(100)
    counter = 1
    monitor.poll(130)
    assert not messages(box)
    faults["futures"] = True
    monitor.poll(160)
    content = json.loads(messages(box)[0]["payload"])["text"]["content"]
    assert "418" in content and "交易异常" in content and "账本持仓 0" in content
    RuntimeMonitor(box, observer).poll(190)
    assert len(messages(box)) == 1
    faults["futures"] = False
    monitor.poll(220)
    monitor.poll(250)
    assert len(messages(box)) == 1
    monitor.poll(280)
    assert "监控恢复" in messages(box)[1]["payload"]
    faults["futures"] = True
    monitor.poll(310)
    assert len(messages(box)) == 3


def test_state_and_notification_are_atomic_on_enqueue_failure(tmp_path, monkeypatch):
    box = Outbox(tmp_path / "queue.sqlite")
    monitor = RuntimeMonitor(
        box, lambda market, now, prior: snapshot(market, now, market == "futures")
    )
    enqueue = box.enqueue

    def fail(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(box, "enqueue", fail)
    with pytest.raises(sqlite3.OperationalError):
        monitor.poll(100)
    assert not messages(box)
    with box.connect() as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    monkeypatch.setattr(box, "enqueue", enqueue)
    monitor.poll(110)
    assert len(messages(box)) == 1


def message_text(box):
    return [json.loads(row["payload"])["text"]["content"] for row in messages(box)]


def test_native_ledger_events_catch_up_and_do_not_repeat_after_restart(tmp_path):
    path = tmp_path / "trades.sqlite"
    create_ledger(path)
    box = Outbox(tmp_path / "queue.sqlite")

    def observer(market, now, prior):
        result = snapshot(market, now)
        if market == "futures":
            result["ledger"] = ledger_snapshot(path, since=(prior or {}).get("order_scan_at"))
        return result

    monitor = RuntimeMonitor(box, observer)
    monitor.poll(100)
    assert not messages(box)  # Historical fills and a pending zero-fill order are silent.

    def write(sql):
        with sqlite3.connect(path) as db:
            db.executescript(sql)

    write("""UPDATE orders SET filled=2, average=10,
             order_update_date='1970-01-01 00:02:00' WHERE id=6;""")
    monitor.poll(130)
    assert "开仓部分成交" in message_text(box)[-1]
    assert "累计 2 SOL @ 10" in message_text(box)[-1]
    assert "EMA20 的 97.8%" in message_text(box)[-1]
    write("""UPDATE orders SET filled=3, average=10.1, ft_is_open=0, status='closed',
             order_filled_date='1970-01-01 00:02:25' WHERE id=6;""")
    RuntimeMonitor(box, observer).poll(160)
    assert "开仓成交" in message_text(box)[-1]
    assert "累计 3 SOL @ 10.1" in message_text(box)[-1]
    write("""INSERT INTO orders
        (id,ft_trade_id,filled,ft_is_open,status,ft_order_side,order_id,average,
         order_date,order_filled_date,order_update_date) VALUES
      (7,3,2,0,'closed','buy','new1',9.8,'1970-01-01 00:02:51',
       '1970-01-01 00:02:53','1970-01-01 00:02:53'),
      (8,3,2,0,'canceled','sell','new2',10.1,'1970-01-01 00:03:01',
       '1970-01-01 00:03:04','1970-01-01 00:03:04');""")
    monitor.poll(190)
    assert "加仓成交" in message_text(box)[-2]
    assert "减仓部分成交，余量未成交" in message_text(box)[-1]
    assert "已实现" not in message_text(box)[-1]
    write("""INSERT INTO orders
        (id,ft_trade_id,filled,ft_is_open,status,ft_order_side,order_id,average,
         order_date,order_filled_date,order_update_date) VALUES
      (9,3,3,0,'closed','sell','new3',10.2,'1970-01-01 00:04:01',
       '1970-01-01 00:04:15','1970-01-01 00:04:15');
      UPDATE trades SET is_open=0, close_profit_abs=.3, exit_reason='exit_long_tc_o_3'
      WHERE id=3;""")
    monitor.poll(400)
    assert "平仓成交" in message_text(box)[-1]
    assert "本笔交易累计已实现 +0.30 USDT" in message_text(box)[-1]
    assert "收盘在 EMA200 上方" in message_text(box)[-1]
    assert len(messages(box)) == 5
    RuntimeMonitor(box, observer).poll(430)
    assert len(messages(box)) == 5


def test_reject_detection_and_cooling_off_deduplication(tmp_path):
    now = datetime(2026, 11, 25, 12, 1, tzinfo=TZ).timestamp()
    log = tmp_path / "application.log"
    log.write_text(
        "2026-11-25 12:00:30,000 - freqtrade.freqtradebot - WARNING - "
        "Unable to create trade for UNI/USDT:USDT: Could not place buy order: "
        '{"code":-4192,"msg":"secret=value"}\n'
    )
    facts = log_snapshot(log, now, 1, now - 300, {})
    data = snapshot("futures", now)
    data["rejections"] = facts["rejections"]
    data["ledger"]["order_activity"] = []

    def observer(market, t, prior):
        return data if market == "futures" else snapshot(market, t)

    box = Outbox(tmp_path / "queue.sqlite")
    monitor = RuntimeMonitor(box, observer)
    monitor.poll(now)
    assert not messages(box)
    data["ledger"]["order_activity"] = []
    monitor.poll(now + 30)
    assert "开仓委托被拒｜UNI/USDT" in message_text(box)[0]
    assert "交易所冷静期" in message_text(box)[0] and "secret" not in message_text(box)[0]
    data["ledger"]["order_activity"] = []
    RuntimeMonitor(box, observer).poll(now + 60)
    assert len(messages(box)) == 1


def test_fast_retry_and_persistently_open_exit(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    data = snapshot("futures", 1000)
    data["rejections"] = {
        "cooloff": {
            "pair": "SOL/USDT",
            "side": "entry",
            "code": "-4192",
            "first": 890,
            "last": 910,
        },
    }
    orders = [
        {"pair": "SOL/USDT", "entry": True, "is_open": False, "created_at": 920, "filled": 0},
        {
            "pair": "ETH/USDT",
            "entry": False,
            "is_open": True,
            "created_at": 690,
            "filled": 0,
            "order_id": "exit-1",
            "order_tag": "roi",
            "trade_id": 2,
        },
    ]

    def observer(market, now, prior):
        if market == "futures":
            data["ledger"]["order_activity"] = orders
            return data
        return snapshot(market, now)

    monitor = RuntimeMonitor(box, observer)
    monitor.poll(1000)
    assert len(messages(box)) == 1
    assert "退出委托待成交超过 5 分钟｜ETH/USDT" in message_text(box)[0]
    monitor.poll(1030)
    assert len(messages(box)) == 1


def test_order_reason_is_observed_once_and_reused_after_log_rotation(tmp_path):
    path = tmp_path / "trades.sqlite"
    create_ledger(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE trades SET pair='SOL/USDT:USDT' WHERE id=3")

    log = tmp_path / "freqtrade.log"
    now = datetime(2026, 11, 25, 21, 0, tzinfo=TZ).timestamp()
    log.write_text(
        "2026-11-25 21:00:08,000 - NostalgiaForInfinityX8 - INFO - "
        "Grinding entry (grind_5_entry) [2026-11-25 13:00:08+00:00] [SOL/USDT:USDT] "
        "| Rate: 0.2021 | Profit (stake): -1.999 | Profit: -12.30% | Tag: g7\n"
    )
    box = Outbox(tmp_path / "messages.sqlite")

    def observer(market, at, prior):
        current = snapshot(market, at)
        if market == "futures":
            current["ledger"] = ledger_snapshot(path, since=(prior or {}).get("order_scan_at"))
            current["decisions"] = log_snapshot(log, at, 1, now - 30, {})["decisions"]
        return current

    RuntimeMonitor(box, observer).poll(now)
    assert not messages(box)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            UPDATE orders SET filled=1, ft_is_open=0 WHERE id=6;
            INSERT INTO orders
              (id,ft_trade_id,filled,ft_is_open,status,ft_order_side,order_id,
               order_date,order_update_date,ft_order_tag,ft_amount,price,order_type)
            VALUES (7,3,0,1,'open','buy','new-7','2026-11-25 13:00:10',
                    '2026-11-25 13:00:10','grind_5_entry',38.9,0.2021,'limit');
        """)
    RuntimeMonitor(box, observer).poll(now + 15)
    submitted = message_text(box)[-1]
    assert "加仓委托已提交 · 待成交" in submitted
    assert "委托 38.9 SOL @ 0.2021" in submitted
    assert "第 5 组分批补仓" in submitted
    assert "策略盈亏比 -12.30%" in submitted and "补仓子信号 g7" in submitted
    before = len(messages(box))
    log.write_text("")
    RuntimeMonitor(box, observer).poll(now + 30)
    assert len(messages(box)) == before
    with sqlite3.connect(path) as conn:
        conn.execute("""UPDATE orders SET filled=38.9,average=0.2022,ft_is_open=0,status='closed',
                    order_filled_date='2026-11-25 13:15:00' WHERE id=7""")
    RuntimeMonitor(box, observer).poll(now + 910)
    fill = message_text(box)[-1]
    assert len(messages(box)) == before + 1
    assert "加仓成交" in fill and "@ 0.2022" in fill
    assert "触发时日志：判断参考价 0.2021" in fill


def test_no_misattribution_of_unrelated_or_future_observations():
    from deploy.live_prepare_20261109.notification_reasons import order_decision, reason_lines

    order = {"pair": "SOL/USDT:USDT", "order_tag": "grind_5_entry", "created_at": 1000}
    record = {"pair": order["pair"], "tag": "grind_5_entry", "stamp": 1001, "profit": "-30.0"}
    assert (
        order_decision(order, [record, {**record, "pair": "UNI/USDT:USDT", "stamp": 999}]) is None
    )
    assert order_decision(order, [{**record, "stamp": 999}, {**record, "stamp": 998}]) is None
    assert "未收录" in reason_lines("unknown_native_reason")[0]
    assert "缺少" in reason_lines(None)[0]
    assert "达到策略第 3 档降风险条件" in reason_lines("derisk_level_3")[0]
    assert "MFI14 > 84" in reason_lines("exit_long_rapid_rpd_2 ( 102 )")[0]


def test_raw_entry_catalog_matches_pinned_rules_and_explains_multi_tag():
    from deploy.live_prepare_20261109.notification_reasons import reason_lines, verified_rules
    from deploy.live_prepare_20261109.notification_rule_catalog import (
        STRATEGY_SOURCE,
        build_catalog,
    )

    expected = build_catalog(STRATEGY_SOURCE)["entries"]
    assert verified_rules() == expected
    assert len(expected) >= 60
    reasons = reason_lines("101 120 101")
    assert len(reasons) == 2
    assert "SMA16 的 94.6%" in reasons[0] and "EMA20 的 97.8%" in reasons[1]
    assert "源码条件之一" in reason_lines("543")[0]
