"""Read-only engineering probes cannot inherit trading state or send HTTP writes."""
# ruff: noqa: S101

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
import requests
from ccxt.async_support.base.exchange import Exchange as AsyncExchange
from ccxt.base.exchange import Exchange as SyncExchange

from deploy.aliyun_20261111.native_probe import (
    ProbeWriteBlocked,
    profile_config,
    readonly_transport,
    validate_isolation,
)


def test_profile_preserves_finance_but_isolates_all_mutable_account_state():
    original, destination = Path("/opt/live/spot"), Path("/opt/probe/spot")
    live = {
        "dry_run": False, "live_capital_mode": "available_balance",
        "max_open_trades": 6, "tradable_balance_ratio": 0.99,
        "stake_amount": "unlimited", "stake_currency": "USDT",
        "exchange": {"key": "private-key", "secret": "private-secret",
                     "pair_whitelist": ["BTC/USDT"]},
        "db_url": "sqlite:////opt/live/spot/trades.sqlite",
        "profit_cache": "/opt/live/spot/profit.json",
        "daily_snapshot_file": "/opt/published/pairs.json",
        "webhook": {"enabled": True, "url": "private-token"},
    }
    saved = deepcopy(live)
    config = profile_config(live, original, destination)
    assert live == saved
    assert config["db_url"] == "sqlite:////opt/probe/spot/probe.sqlite"
    assert config["profit_cache"] == "/opt/probe/spot/profit.json"
    assert config["daily_snapshot_file"] == live["daily_snapshot_file"]
    assert "key" not in config["exchange"] and "secret" not in config["exchange"]
    for key in ("dry_run", "live_capital_mode", "max_open_trades", "tradable_balance_ratio",
                "stake_amount", "stake_currency"):
        assert config[key] == live[key]
    assert config["initial_state"] == "stopped"
    assert not config["cancel_open_orders_on_exit"]
    assert config["webhook"] == {"enabled": False}
    assert config["exchange"]["pair_whitelist"] == ["BTC/USDT"]


def test_disabled_probe_rpc_sections_pass_the_native_schema():
    from freqtrade.config_schema import CONF_SCHEMA
    from freqtrade.configuration.config_validation import FreqtradeValidator

    config = profile_config(
        {"dry_run": False, "live_capital_mode": "available_balance", "exchange": {}},
        Path("/original"), Path("/probe"),
    )
    for key in ("api_server", "telegram", "webhook"):
        FreqtradeValidator(CONF_SCHEMA["properties"][key]).validate(config[key])


@pytest.mark.parametrize("change", [
    {"db_url": "sqlite:////original/trades.sqlite"},
    {"user_data_dir": "/original"}, {"initial_state": "running"},
    {"cancel_open_orders_on_exit": True}, {"api_server": {"enabled": True}},
    {"webhook": {"enabled": True}}, {"telegram": {"enabled": True}},
    {"external_message_consumer": {"enabled": True}},
])
def test_any_overridden_isolation_setting_rejects_native_initialization(change, tmp_path):
    derived = {"db_url": "sqlite:///probe.sqlite", "user_data_dir": str(tmp_path),
               "initial_state": "stopped", "cancel_open_orders_on_exit": False}
    validate_isolation(derived, derived, tmp_path)
    with pytest.raises(ValueError, match="isolation"):
        validate_isolation({**derived, **change}, derived, tmp_path)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_http_transport_blocks_writes_before_original_is_called(monkeypatch, method):
    calls = []

    def sync(*args, **kwargs):
        calls.append("sync")
        return "allowed"

    async def asynchronous(*args, **kwargs):
        calls.append("async")
        return "allowed"

    monkeypatch.setattr(requests.Session, "request", sync)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", asynchronous)
    monkeypatch.setattr(SyncExchange, "fetch", sync)
    monkeypatch.setattr(AsyncExchange, "fetch", asynchronous)
    with readonly_transport() as counters:
        with pytest.raises(ProbeWriteBlocked):
            requests.Session().request(method, "https://example.invalid/order")
        with pytest.raises(ProbeWriteBlocked):
            SyncExchange.fetch(object(), "https://example.invalid/order", method)
        with pytest.raises(ProbeWriteBlocked):
            asyncio.run(AsyncExchange.fetch(object(), "https://example.invalid/order", method))
        with pytest.raises(ProbeWriteBlocked):
            asyncio.run(aiohttp.ClientSession._request(
                object(), method, "https://example.invalid/order",
            ))
        assert calls == [] and counters["blocked_writes"] == 4
        assert requests.Session().request("GET", "https://example.invalid/account") == "allowed"
        assert SyncExchange.fetch(object(), "https://example.invalid/candles") == "allowed"
        assert asyncio.run(AsyncExchange.fetch(
            object(), "https://example.invalid/candles",
        )) == "allowed"
        assert asyncio.run(aiohttp.ClientSession._request(
            object(), "GET", "https://example.invalid/account",
        )) == "allowed"
        assert counters["get_calls"] == 4
    assert requests.Session.request is sync
    assert AsyncExchange.fetch is asynchronous


@pytest.mark.parametrize("ledger_changed", [False, True])
def test_concurrent_recheck_cannot_publish_success_after_original_ledger_changes(
    tmp_path, monkeypatch, ledger_changed,
):
    from deploy.aliyun_20261111 import native_probe

    original = tmp_path / "original"
    original.mkdir()
    ledger = original / "trades.sqlite"
    ledger.write_bytes(b"original ledger")
    before = hashlib.sha256(ledger.read_bytes()).digest()
    folder = tmp_path / "native-capacity-test/spot"
    folder.mkdir(parents=True)
    (folder.parent / "recheck").touch()
    clock = {"now": 0}

    def sleep(seconds):
        clock["now"] += 2

    monkeypatch.setattr(native_probe, "time", SimpleNamespace(
        monotonic=lambda: clock["now"], sleep=sleep,
    ))

    def analyze(bot, expected):
        if ledger_changed:
            ledger.write_bytes(b"unexpected state modification")

    monkeypatch.setattr(native_probe, "analyze_pipeline", analyze)
    arguments = (object(), 36, folder, {"blocked_writes": 0}, original, before, 1)
    if ledger_changed:
        with pytest.raises(ValueError, match="Original ledger changed"):
            native_probe.hold_and_recheck(*arguments)
        assert not (folder / "rechecked.json").exists()
    else:
        native_probe.hold_and_recheck(*arguments)
        assert json.loads((folder / "rechecked.json").read_text())["concurrent_recheck_passed"]
