"""Delivery contract and official native webhook rendering checks without external messages."""


# ruff: noqa: S101, RUF001 -- Pytest assertions and Chinese notification fixtures.

import json
from datetime import UTC, datetime

import pytest
import requests

from deploy.live_prepare_20261109.notify_relay import Outbox
from deploy.live_prepare_20261109.prepare import webhook_config
from freqtrade.enums import RPCMessageType
from freqtrade.rpc.rpc_types import (
    RPCCancelMsg,
    RPCEntryMsg,
    RPCExitCancelMsg,
    RPCExitMsg,
    RPCLiquidationWarningMsg,
    RPCProtectionMsg,
    RPCStatusMsg,
)
from freqtrade.rpc.webhook import Webhook


def payload(text="测试通知"):
    return {"msgtype": "text", "text": {"content": text}}


def row(box):
    with box.connect() as conn:
        return dict(conn.execute("SELECT * FROM messages ORDER BY id LIMIT 1").fetchone())


def test_business_error_is_persisted_and_retried_after_restart(tmp_path):
    path = tmp_path / "queue.sqlite"
    box = Outbox(path)
    box.enqueue(payload(), now=100)
    box.deliver_one(lambda _: 45009, now=100)
    assert row(box)["state"] == "pending"
    assert row(box)["attempts"] == 1
    restored = Outbox(path)
    assert not restored.deliver_one(lambda _: 0, now=101)
    assert restored.deliver_one(lambda _: 0, now=107)
    assert row(restored)["state"] == "sent"
    assert row(restored)["result"] == "errcode:0"


def test_transport_error_never_exposes_endpoint_or_drops_message(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue(payload(), now=100)

    def fail(_):
        raise requests.Timeout("private webhook URL must not be persisted")

    box.deliver_one(fail, now=100)
    assert row(box)["state"] == "pending"
    assert row(box)["result"] == "Timeout"


def test_boolean_false_is_not_a_success_code_and_attempts_are_bounded(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue(payload(), now=100)
    for i in range(8):
        box.deliver_one(lambda _: False, now=100 + 400 * i)
    assert row(box)["state"] == "dead"
    assert row(box)["attempts"] == 8


def test_local_rate_limit_and_duplicate_looking_events_are_not_lost(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue(payload(), now=100)
    box.enqueue(payload(), now=100)
    assert box.deliver_one(lambda _: 0, now=100)
    assert not box.deliver_one(lambda _: 0, now=101)
    assert box.deliver_one(lambda _: 0, now=104)
    assert box.status() == {"sent": 2}


def test_utf8_limit_and_invalid_payload(tmp_path):
    box = Outbox(tmp_path / "queue.sqlite")
    box.enqueue(payload("汉" * 2000), now=100)
    assert len(json.loads(row(box)["payload"])["text"]["content"].encode()) < 1800
    with pytest.raises(ValueError):
        box.enqueue({"msgtype": "image"}, now=100)
    with pytest.raises(ValueError):
        box.enqueue([], now=100)


@pytest.mark.parametrize("market", ["spot", "futures"])
def test_all_native_event_templates_render_without_missing_fields(market):
    config = {"webhook": webhook_config(market, "http://127.0.0.1:1/not-sent")}
    native = Webhook(None, config)
    fields = {
        "trade_id": 1,
        "pair": "BTC/USDT",
        "direction": "Long",
        "leverage": 1.0,
        "amount": 0.01,
        "order_rate": 100.0,
        "open_rate": 99.0,
        "stake_amount": 10.0,
        "stake_currency": "USDT",
        "sub_trade": True,
        "reason": "timeout",
        "profit_amount": -0.25,
        "profit_ratio": -0.025,
        "exit_reason": "stop_loss",
        "status": "验收测试，交易未启动",
        "side": "long",
        "lock_end_time": "later",
        "current_rate": 95.0,
        "liquidation_price": 50.0,
        "remaining_ratio": 0.5,
        "open_date": datetime.now(UTC),
    }
    events = [key for key in config["webhook"] if isinstance(config["webhook"][key], dict)]
    assert len(events) == 13
    contracts = {
        "entry": RPCEntryMsg,
        "entry_fill": RPCEntryMsg,
        "entry_cancel": RPCCancelMsg,
        "exit": RPCExitMsg,
        "exit_fill": RPCExitMsg,
        "exit_cancel": RPCExitCancelMsg,
        "protection_trigger": RPCProtectionMsg,
        "protection_trigger_global": RPCProtectionMsg,
        "liquidation_warning": RPCLiquidationWarningMsg,
    }
    for event in events:
        contract = contracts.get(event, RPCStatusMsg)
        # Exclude unrelated fields so a template cannot rely on another event's data.
        message = {k: v for k, v in fields.items() if k in contract.__annotations__}
        message["type"] = RPCMessageType(event)
        rendered = native.recursive_format(native._get_value_dict(message), message)
        assert rendered["msgtype"] == "text"
        assert ("现货实盘" if market == "spot" else "永续实盘") in rendered["text"]["content"]
    content = native.recursive_format(config["webhook"]["exit_fill"], fields)["text"]["content"]
    assert "-0.2500 USDT (-2.50%)" in content
