"""Cross-process admission, transport hooks and persistent ban tests, offline."""

# ruff: noqa: S101

import asyncio
import json
import multiprocessing
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import ccxt
import ccxt.async_support as ccxt_async
import pytest
import requests

from freqtrade.exchange.exchange import Exchange
from freqtrade.exchange.request_guard import (
    GuardedSession,
    SharedRequestGuard,
    cache_identity,
    install,
)


def attempt(path):
    guard = SharedRequestGuard(path)
    return guard.claim(1, now=100)[0] is not None


def test_independent_processes_share_one_admission_slot(tmp_path):
    path = str(tmp_path / "guard.sqlite")
    SharedRequestGuard(path)
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as pool:
        assert sum(pool.map(attempt, [path] * 8)) == 1


def test_weight_spacing_inflight_and_release(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite", weight_per_minute=600, in_flight=1)
    ticket, delay = guard.claim(20, now=100)
    assert ticket and delay == 0
    assert guard.claim(1, now=101) == (None, 1)
    assert guard.claim(1, now=102)[0] is None  # In-flight concurrency cap.
    guard.release(ticket)
    assert guard.claim(1, now=102)[0]


def test_418_deadline_is_persistent_cannot_be_shortened_by_success_or_restart(tmp_path):
    path = tmp_path / "g.sqlite"
    guard = SharedRequestGuard(path)
    guard.response(418, {}, "banned until 2000000000000", now=100)
    restored = SharedRequestGuard(path)
    assert restored.claim(1, now=101)[1] == 2_000_000_005 - 101
    restored.response(200, {}, "", now=102)
    restored.response(429, {"Retry-After": "30"}, "", now=102)
    assert restored.status()["cooldown"] == 2_000_000_005
    assert "banned" not in json.dumps(restored.status())


def test_missing_deadline_429_retry_after_and_high_ip_weight(tmp_path):
    guard = SharedRequestGuard(tmp_path / "a.sqlite")
    guard.response(429, {"Retry-After": "90"}, "", now=100)
    assert guard.status()["cooldown"] == 195
    other = SharedRequestGuard(tmp_path / "b.sqlite")
    other.response(418, {}, "", now=100)
    assert other.status()["cooldown"] == 1005
    headroom = SharedRequestGuard(tmp_path / "c.sqlite")
    headroom.response(200, {"X-MBX-USED-WEIGHT-1M": "1000"}, "", now=100)
    assert headroom.status()["cooldown"] == 125


def test_public_cache_single_flight_and_no_private_price_or_order_cache(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    key, ttl = cache_identity(
        "klines", "fapiPublic", "GET", {"symbol": "BTCUSDT", "interval": "5m"}
    )
    assert ttl == 10
    assert guard.cache_claim(key, "a", time.time())[0] == "owner"
    assert guard.cache_claim(key, "b", time.time())[0] == "wait"
    guard.cache_finish(key, "a", [[1, 2]], ttl)
    assert guard.cache_claim(key, "b", time.time()) == ("hit", [[1, 2]])
    assert cache_identity("account", "fapiPrivate", "GET", {}) == (None, 0)
    assert cache_identity("klines", "fapiPublic", "POST", {}) == (None, 0)
    assert cache_identity("ticker/price", "fapiPublic", "GET", {}) == (None, 0)
    assert cache_identity("klines", "fapiPublic", "GET", {"apiKey": "secret"}) == (None, 0)


def test_sync_installed_ccxt_signs_after_shared_admission_and_uses_public_cache(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    client = ccxt.binance({"enableRateLimit": True})
    actual = []

    def fetch(url, method="GET", headers=None, body=None):
        actual.append(url)
        return {"symbols": []}

    client.fetch = fetch
    install(client, sync=True, guard=guard)
    assert client.publicGetExchangeInfo() == {"symbols": []}
    assert client.publicGetExchangeInfo() == {"symbols": []}
    assert len(actual) == 1
    assert guard.status()["transports"] == 1 and guard.status()["cache_hits"] == 1


@pytest.mark.asyncio
async def test_async_418_callback_prevents_any_followup_transport_and_cancels_cleanly(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    client = ccxt_async.binance({"enableRateLimit": True})
    sent = []

    async def fetch(url, method="GET", headers=None, body=None):
        sent.append(url)
        client.on_rest_response(
            418, "banned", url, method, {}, "banned until 2000000000000", {}, None
        )
        raise ccxt.DDoSProtection("simulated")

    client.fetch = fetch
    install(client, sync=False, guard=guard)
    try:
        with pytest.raises(ccxt.DDoSProtection):
            await client.fapiPublicGetTime()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.fapiPublicGetTime(), timeout=0.1)
        assert len(sent) == 1
        with guard.connect() as conn:
            assert conn.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_two_async_ccxt_instances_single_flight_share_market_response(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    clients = [ccxt_async.binance(), ccxt_async.binance()]
    count = 0

    async def fetch(*args, **kwargs):
        nonlocal count
        count += 1
        await asyncio.sleep(0.02)
        return {"symbols": [{"symbol": "BTCUSDT"}]}

    for client in clients:
        client.fetch = fetch
        install(client, sync=False, guard=guard)
    try:
        values = await asyncio.gather(*(x.fapiPublicGetExchangeInfo() for x in clients))
        assert count == 1 and values[0] == values[1]
    finally:
        await asyncio.gather(*(x.close() for x in clients))


def test_auxiliary_reader_rejects_active_ban_before_network_and_forbids_writes(
    tmp_path, monkeypatch
):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    guard.block_until(time.time() + 3600, "SEEDED_LOG_418")
    monkeypatch.setattr("freqtrade.exchange.request_guard.configured_guard", lambda: guard)
    monkeypatch.setattr(
        requests.Session, "request", lambda *a, **k: pytest.fail("Network forbidden")
    )
    with GuardedSession() as session:
        with pytest.raises(requests.RequestException, match="cooldown"):
            session.get("https://fapi.binance.com/fapi/v1/time")
        with pytest.raises(requests.RequestException, match="GET only"):
            session.post("https://fapi.binance.com/fapi/v1/order")


def test_candle_cache_key_changes_at_close_boundary(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 299)
    before, _ = cache_identity("klines", "fapiPublic", "GET", {"interval": "5m"})
    monkeypatch.setattr(time, "time", lambda: 300)
    after, _ = cache_identity("klines", "fapiPublic", "GET", {"interval": "5m"})
    assert before != after


def test_cache_does_not_mix_origins_or_different_historical_ranges():
    a, _ = cache_identity("klines", "fapiPublic", "GET", {"startTime": 1}, "mainnet")
    b, _ = cache_identity("klines", "fapiPublic", "GET", {"startTime": 1}, "testnet")
    c, _ = cache_identity("klines", "fapiPublic", "GET", {"startTime": 2}, "mainnet")
    assert len({a, b, c}) == 3


def test_private_signing_occurs_after_admission_and_private_data_never_cached(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite", requests_per_minute=60000)
    client = ccxt.binance({"apiKey": "offline-fixture", "secret": "offline-fixture"})
    admitted = []
    original_acquire = guard.acquire

    def acquire(weight, **kwargs):
        token = original_acquire(weight, **kwargs)
        admitted.append(time.time())
        return token

    guard.acquire = acquire
    original_sign = client.sign

    def sign(*args, **kwargs):
        assert admitted
        return original_sign(*args, **kwargs)

    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        return {"balances": []}

    client.sign = sign
    client.fetch = fetch
    install(client, sync=True, guard=guard)
    client.privateGetAccount()
    client.privateGetAccount()
    assert len(calls) == 2 and guard.status()["cache_hits"] == 0


def test_fail_closed_on_corrupt_database_no_transport(tmp_path):
    path = tmp_path / "g.sqlite"
    guard = SharedRequestGuard(path)
    path.write_text("corrupt")
    with pytest.raises(sqlite3.DatabaseError):
        guard.claim(1)


def test_native_exchange_initializer_installs_guard_when_opted_in(tmp_path, monkeypatch):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    monkeypatch.setattr("freqtrade.exchange.request_guard.configured_guard", lambda: guard)
    owner = SimpleNamespace(_ccxt_params={}, get_option=lambda key: False)
    client = Exchange._init_ccxt(owner, {"name": "binance"}, True, {})
    assert client._shared_request_guard_installed is True
    assert client.enableRateLimit is True


def test_ccxt_internal_retries_cannot_bypass_shared_admission(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    client = ccxt.binance({"options": {"maxRetriesOnFailure": 3}})
    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        raise ccxt.RequestTimeout("offline")

    client.fetch = fetch
    install(client, sync=True, guard=guard)
    with pytest.raises(ccxt.RequestTimeout):
        client.fapiPublicGetTime({"maxRetriesOnFailure": 3})
    assert len(calls) == 3 and guard.status()["transports"] == 3


def test_auxiliary_418_response_updates_same_shared_cooldown(tmp_path, monkeypatch):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    monkeypatch.setattr("freqtrade.exchange.request_guard.configured_guard", lambda: guard)
    response = requests.Response()
    response.status_code = 418
    response._content = b'{"msg":"banned until 2000000000000"}'
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    with GuardedSession() as session:
        session.get("https://fapi.binance.com/fapi/v1/time")
        with pytest.raises(requests.RequestException):
            session.get("https://fapi.binance.com/fapi/v1/time")
    assert calls == [1] and guard.status()["cooldown"] == 2_000_000_005


def test_expired_cache_lease_can_be_recovered_but_live_lease_not_stolen(tmp_path):
    guard = SharedRequestGuard(tmp_path / "g.sqlite")
    start = time.time() - 121
    assert guard.cache_claim("key", "owner-a", start)[0] == "owner"
    assert guard.cache_claim("key", "owner-b", start + 1)[0] == "wait"
    assert guard.cache_claim("key", "owner-b", start + 121)[0] == "owner"
    guard.cache_finish("key", "owner-a", [1], 10)
    guard.cache_finish("key", "owner-b", [2], 10)
    assert guard.cache_claim("key", "owner-c", time.time()) == ("hit", [2])
