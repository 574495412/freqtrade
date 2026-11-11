"""Native presentation, read-only diagnostics and durable state-change delivery."""

# ruff: noqa: S101, RUF001 -- Pytest and Chinese notification fixtures.

import json
import sqlite3
from datetime import datetime

import pytest

from deploy.live_prepare_20261109.notification_design import native_card, redact, runtime_card
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
    ("event", "adjust", "expected"),
    [
        ("entry", False, "开仓委托（待成交）"),
        ("entry_fill", False, "开仓成交"),
        ("entry_fill", True, "加仓成交"),
        ("entry_cancel", True, "加仓委托取消"),
        ("exit", False, "平仓委托（待成交）"),
        ("exit_fill", True, "减仓成交"),
        ("exit_cancel", False, "平仓委托取消"),
    ],
)
def test_running_native_template_is_adapted_without_trader_restart(
    tmp_path, event, adjust, expected
):
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
            "sub_trade": adjust,
            "reason": "timeout",
            "profit_amount": -0.2,
            "profit_ratio": -0.025,
            "exit_reason": "stop_loss",
        }
    )
    payload = json.loads(messages(box)[0]["payload"])
    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert expected in content and "ADA/USDT:USDT" in content and "做空" in content
    assert "交易编号 7" in content
    if event.startswith("exit"):
        assert "-0.2000 USDT (-2.50%)" in content
        assert ("已实现盈亏" if event == "exit_fill" else "预计盈亏") in content


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


def test_markdown_is_bounded_and_startup_never_advertises_unlimited_wallet(tmp_path):
    payload = native_card("[NFI X8｜现货实盘] 程序启动：Stake unlimited USDT")
    assert "unlimited" not in payload["markdown"]["content"]
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue({"msgtype": "markdown", "markdown": {"content": "汉" * 2000}})
    content = json.loads(messages(box)[0]["payload"])["markdown"]["content"]
    assert len(content.encode()) <= 3800


def create_ledger(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE trades(id INTEGER,is_open INTEGER,close_profit_abs REAL,
                pair TEXT,is_short INTEGER,stake_amount REAL);
            CREATE TABLE orders(ft_trade_id INTEGER,filled REAL,ft_is_open INTEGER,
                status TEXT,ft_order_side TEXT);
            INSERT INTO trades VALUES(1,0,-2,'BTC/USDT',0,10);
            INSERT INTO trades VALUES(2,0,1,'ETH/USDT',1,10);
            INSERT INTO trades VALUES(3,1,NULL,'SOL/USDT',0,5);
            INSERT INTO orders VALUES(1,1,0,'closed','buy');
            INSERT INTO orders VALUES(1,1,0,'closed','buy');
            INSERT INTO orders VALUES(1,2,0,'closed','sell');
            INSERT INTO orders VALUES(2,1,0,'closed','sell');
            INSERT INTO orders VALUES(2,1,0,'closed','buy');
            INSERT INTO orders VALUES(3,0,1,'open','buy');
        """)


def test_native_ledger_closed_pnl_short_entries_and_additions_are_read_only(tmp_path):
    path = tmp_path / "trades.sqlite"
    create_ledger(path)
    before = path.read_bytes()
    ledger = ledger_snapshot(path)
    assert ledger["closed_trades"] == 2 and ledger["wins"] == 1
    assert ledger["closed_pnl"] == -1
    assert ledger["orders"] == 6 and ledger["filled_orders"] == 5
    assert ledger["open_orders"] == 1 and ledger["open_trades"] == 1
    assert ledger["additions"] == 1
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
    again = log_snapshot(path, now + 400, 42, now - 100, {"pid": 42, **result})
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
        "spot", 1000, {"pid": 42, "heartbeat": 700, "native_state": "RUNNING"}, root=tmp_path
    )
    assert result["heartbeat_age"] == 300 and "heartbeat" in result["faults"]
    assert "process" not in result["faults"]


def snapshot(market, now, fault=False):
    return {
        "market": market,
        "observed": now,
        "pid": 42,
        "heartbeat_age": 1,
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


def test_observer_initial_changes_recovery_and_restart_dedup(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    faults = {"futures": True}

    def observer(market, now, prior):
        return snapshot(market, now, faults.get(market, False))

    monitor = RuntimeMonitor(box, observer)
    monitor.poll(100)
    assert len(messages(box)) == 1
    content = json.loads(messages(box)[0]["payload"])["markdown"]["content"]
    assert "418" in content and "尚无已平仓" in content and "100.0%" not in content
    assert len(content.encode()) <= 3800
    monitor.poll(130)
    RuntimeMonitor(box, observer).poll(160)
    assert len(messages(box)) == 1
    faults["futures"] = False
    monitor.poll(190)
    assert len(messages(box)) == 2
    faults["futures"] = True
    monitor.poll(220)
    assert len(messages(box)) == 3  # Same warning recurring after recovery is a new event.


def test_runtime_card_reports_available_balance_without_historical_fixed_limit():
    from deploy.live_prepare_20261109.notification_design import runtime_card

    current = snapshot("spot", 100)
    current["contract"].update(
        capital_mode="available_balance", ceiling=None, budget=None, tradable_balance_ratio=0.99
    )
    content = runtime_card([current])["markdown"]["content"]
    assert "99%" in content and "无固定上限" in content and "100U" not in content


def test_state_and_notification_are_atomic_on_enqueue_failure(tmp_path, monkeypatch):
    box = Outbox(tmp_path / "queue.sqlite")
    monitor = RuntimeMonitor(box, lambda market, now, prior: snapshot(market, now))
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


def test_snapshot_profit_does_not_misrepresent_open_pnl_or_account_equity():
    data = snapshot("spot", 100)
    data["ledger"].update(closed_trades=2, wins=1, closed_pnl=-1.25)
    text = runtime_card([data])["markdown"]["content"]
    assert "-1.2500U" in text and "50.0%" in text
    assert "未包含未平仓浮盈亏" in text
    assert "账户总收益" in text
