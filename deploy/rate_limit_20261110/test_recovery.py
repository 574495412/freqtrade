"""Fault injection uses native CCXT hooks and native worker recovery, offline."""
# ruff: noqa: S101

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import ccxt
import ccxt.async_support as ccxt_async
import pytest

from freqtrade.exceptions import TemporaryError
from freqtrade.exchange.exchange import Exchange
from freqtrade.exchange.request_guard import GuardStorageUnavailable, SharedRequestGuard, install
from freqtrade.worker import Worker


@pytest.fixture
def guard(tmp_path):
    return SharedRequestGuard(
        tmp_path / "guard.sqlite", weight_per_minute=60000, requests_per_minute=60000
    )


def break_storage(guard, monkeypatch, failures):
    original = guard.connect
    calls = []

    @contextmanager
    def connect():
        calls.append(1)
        if len(calls) <= failures:
            raise sqlite3.OperationalError("unable to open database file")
        with original() as connection:
            yield connection

    monkeypatch.setattr(guard, "connect", connect)
    return calls


def test_storage_transient_failure_reopens_transaction_and_keeps_budget(guard, monkeypatch):
    guard.block_until(100, "test")
    calls = break_storage(guard, monkeypatch, 2)
    assert guard.claim(1, now=90) == (None, 10)
    assert len(calls) == 3
    assert guard.status()["cooldown"] == 100


def test_persistent_storage_failure_blocks_transport_and_native_worker_recovers(guard, monkeypatch):
    calls = break_storage(guard, monkeypatch, 100)
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    client.fetch = Mock(side_effect=AssertionError("transport must remain blocked"))
    install(client, sync=True, guard=guard)
    owner = Mock(
        _api=SimpleNamespace(fetch_l2_order_book=lambda *args: client.fapiPublicGetTime()),
        _has_watch_orderbook=False,
        _ft_has={
            "l2_limit_range": None,
            "l2_limit_range_required": False,
            "l2_limit_upper": None,
            "ws_orderbook_depth": None,
        },
        get_next_limit_in_list=Mock(return_value=5),
    )
    # Exercise the actual native mapping rather than reimplementing it in a test.
    with pytest.raises(TemporaryError, match="GuardStorageUnavailable"):
        Exchange.fetch_l2_order_book.__wrapped__(owner, "DOT/USDT:USDT", 5)
    assert len(calls) == 3
    worker = Worker.__new__(Worker)
    worker.freqtrade = Mock()
    worker.freqtrade.process.side_effect = TemporaryError("storage unavailable")
    sleep = Mock()
    monkeypatch.setattr("freqtrade.worker.time.sleep", sleep)
    worker._process_running()
    sleep.assert_called_once()


def test_storage_exhaustion_does_not_expose_raw_exception(guard, monkeypatch):
    break_storage(guard, monkeypatch, 100)
    with pytest.raises(GuardStorageUnavailable, match="admission blocked"):
        guard.claim(1)


def test_storage_failure_during_insert_rolls_back_before_replay(guard, monkeypatch):
    original = guard.connect
    failures = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            result = self.connection.execute(sql, *args)
            if sql.startswith("INSERT INTO leases") and not failures:
                failures.append(1)
                raise sqlite3.OperationalError("unable to open database file")
            return result

    @contextmanager
    def connect():
        with original() as connection:
            yield Connection(connection)

    monkeypatch.setattr(guard, "connect", connect)
    ticket, delay = guard.claim(1)
    assert ticket and delay == 0
    with original() as connection:
        assert connection.execute("select count(*) from leases").fetchone()[0] == 1


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT"])
def test_write_timeout_is_never_replayed_by_transport_guard(guard, method):
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    client.fetch = transport = Mock(side_effect=ccxt.RequestTimeout("ambiguous exchange result"))
    install(client, sync=True, guard=guard)
    with pytest.raises(ccxt.RequestTimeout):
        client.fetch2("order", ["fapiPrivate"], method, {})
    assert transport.call_count == 1


def test_get_proxy_failure_retries_with_new_admission_and_new_signature(guard):
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    events = []

    def sign(*args, **kwargs):
        events.append(("sign", guard.status()["transports"]))
        return {
            "url": "https://fapi.binance.com/fapi/v1/time",
            "method": "GET",
            "headers": None,
            "body": None,
        }

    client.sign = sign
    client.fetch = transport = Mock(
        side_effect=[ccxt.NetworkError("proxy"), ccxt.RequestTimeout("timeout"), {"serverTime": 1}]
    )
    install(client, sync=True, guard=guard)
    assert client.fapiPublicGetTime() == {"serverTime": 1}
    assert transport.call_count == 3
    assert events == [("sign", 0), ("sign", 1), ("sign", 2)]
    with guard.connect() as connection:
        assert connection.execute("select count(*) from leases").fetchone()[0] == 0


def test_successful_write_response_survives_bookkeeping_failure(guard, monkeypatch):
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})

    def fetch(url, method="GET", headers=None, body=None):
        monkeypatch.setattr(guard, "response", Mock(side_effect=GuardStorageUnavailable("storage")))
        monkeypatch.setattr(guard, "release", Mock(side_effect=GuardStorageUnavailable("storage")))
        client.on_rest_response(200, "OK", url, method, {}, "{}", {}, None)
        return {"orderId": "accepted"}

    client.fetch = transport = Mock(side_effect=fetch)
    install(client, sync=True, guard=guard)
    assert client.fetch2("order", ["fapiPrivate"], "POST", {}) == {"orderId": "accepted"}
    assert transport.call_count == 1


def test_ambiguous_write_timeout_not_masked_by_cleanup(guard, monkeypatch):
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    client.fetch = transport = Mock(side_effect=ccxt.RequestTimeout("original timeout"))
    monkeypatch.setattr(guard, "release", Mock(side_effect=GuardStorageUnavailable("storage")))
    install(client, sync=True, guard=guard)
    with pytest.raises(ccxt.RequestTimeout, match="original timeout"):
        client.fetch2("order", ["fapiPrivate"], "POST", {})
    assert transport.call_count == 1


def test_ban_survives_failed_database_persistence(guard, monkeypatch):
    original = guard.connect
    break_storage(guard, monkeypatch, 100)
    with pytest.raises(GuardStorageUnavailable):
        guard.response(418, {}, "banned until 2000000000000", now=100)
    monkeypatch.setattr(guard, "connect", original)
    assert guard.claim(1, now=200)[1] == 2_000_000_005 - 200
    assert SharedRequestGuard(guard.path).status()["cooldown"] == 2_000_000_005


@pytest.mark.asyncio
async def test_async_get_timeout_recovers_and_write_timeout_is_not_replayed(guard):
    client = ccxt_async.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    calls = []

    async def fetch(url, method="GET", headers=None, body=None):
        calls.append(method)
        if len(calls) == 1 or method != "GET":
            raise ccxt.RequestTimeout("proxy timeout")
        return {"serverTime": 1}

    client.fetch = fetch
    install(client, sync=False, guard=guard)
    try:
        assert await client.fapiPublicGetTime() == {"serverTime": 1}
        with pytest.raises(ccxt.RequestTimeout):
            await client.fetch2("order", ["fapiPrivate"], "POST", {})
        assert calls == ["GET", "GET", "POST"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "error",
    [ccxt.DDoSProtection, ccxt.RateLimitExceeded, ccxt.AuthenticationError, ccxt.PermissionDenied],
)
def test_ban_and_permission_failures_are_not_transport_retried(guard, error):
    client = ccxt.binance({"apiKey": "offline-key", "secret": "offline-secret"})
    client.fetch = transport = Mock(side_effect=error("blocked"))
    install(client, sync=True, guard=guard)
    with pytest.raises(error):
        client.fapiPublicGetTime()
    assert transport.call_count == 1
