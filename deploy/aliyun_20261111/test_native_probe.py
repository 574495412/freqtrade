"""Read-only engineering probes cannot inherit trading state or send HTTP writes."""
# ruff: noqa: S101

import asyncio
from copy import deepcopy
from pathlib import Path

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
