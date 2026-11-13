"""Opt-in, cross-process Binance transport budget and persistent IP cooldown.

Local extension of the native exchange initializer. No signals, positions or
account simulation live here. All Binance hosts using this deployment share one
conservative budget; restarting a client cannot clear a server-imposed ban.
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

import ccxt

from freqtrade.request_guard_core import SharedRequestGuard as CoreRequestGuard
from freqtrade.request_guard_core import retry_storage as retry_storage  # noqa: PLC0414
from freqtrade.request_guard_http import GuardedSession as HTTPGuardedSession


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "user_data/deployment/binance-request-guard.json"
PUBLIC_FAMILIES = {"public", "fapiPublic", "fapiPublicV2", "dapiPublic"}
CACHE_PATHS = {"exchangeInfo", "klines", "markPriceKlines", "indexPriceKlines"}
STORAGE_RETRY_DELAYS = (0.25, 0.75)
GET_RETRY_DELAYS = (0.5, 1.0)


class GuardStorageUnavailable(ccxt.ExchangeNotAvailable):
    """Transient local admission failure; native Freqtrade retries without sending."""


def retry_read(error, method, attempt):
    # Orders/cancels may already have reached Binance. Only GETs can be replayed
    # here; native order reconciliation remains responsible for ambiguous writes.
    return (
        method.upper() == "GET"
        and attempt < len(GET_RETRY_DELAYS)
        and not isinstance(
            error, (ccxt.DDoSProtection, ccxt.RateLimitExceeded, GuardStorageUnavailable)
        )
    )


class SharedRequestGuard(CoreRequestGuard):
    storage_error = GuardStorageUnavailable
    cooldown_error = ccxt.DDoSProtection


def configured_guard():
    if not POLICY.exists():
        return None
    if POLICY.is_symlink() or POLICY.stat().st_mode & 0o077:
        raise ValueError("Binance guard policy must be private")
    policy = json.loads(POLICY.read_text())
    if policy.get("enabled") is not True:
        raise ValueError("Existing Binance guard policy cannot silently disable protection")
    return SharedRequestGuard(
        ROOT / "user_data/deployment/binance-request-guard.sqlite",
        weight_per_minute=policy["weight_per_minute"],
        requests_per_minute=policy["requests_per_minute"],
        in_flight=policy["in_flight"],
    )


def cache_identity(path, api, method, params, namespace=""):
    family = api if isinstance(api, str) else "/".join(api)
    if method != "GET" or family not in PUBLIC_FAMILIES or path not in CACHE_PATHS:
        return None, 0
    if set(params) - {
        "symbol",
        "pair",
        "interval",
        "startTime",
        "endTime",
        "limit",
        "contractType",
    }:
        return None, 0
    # No private data, orders, balances, trades, tickers or order books are cached.
    # Bucket prevents a cache value crossing a candle-close boundary.
    interval = str(params.get("interval", "1m"))
    unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(interval[-1:], 60)
    try:
        seconds = int(interval[:-1]) * unit
    except ValueError:
        seconds = 60
    bucket = int(time.time() // max(1, seconds)) if path != "exchangeInfo" else 0
    key = json.dumps([namespace, family, path, params, bucket], sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest(), 3600 if path == "exchangeInfo" else 10


def install(api, *, sync, guard=None):  # noqa: C901 -- Matching sync/async CCXT hooks.
    """Use CCXT instance hooks, leaving installed CCXT and strategy code intact."""
    if api.id != "binance" or getattr(api, "_shared_request_guard_installed", False):
        return
    guard = configured_guard() if guard is None else guard
    if guard is None:
        return
    weight = ContextVar("binance_request_weight", default=5.0)
    ticket = ContextVar("binance_request_ticket", default=None)
    original_throttle = api.throttle
    original_fetch2 = api.fetch2
    original_fetch = api.fetch
    original_response = api.on_rest_response
    api.enableRateLimit = True
    # A CCXT-internal retry would skip its throttle hook. Native Freqtrade owns
    # retry/reconciliation and must return through shared admission each time.
    api.options["maxRetriesOnFailure"] = 0

    def context(path, family, method, params, config):
        cost = api.calculate_rate_limiter_cost(family, method, path, params, config)
        name = family if isinstance(family, str) else "/".join(family)
        # Installed CCXT Spot costs are fifths of documented request weight.
        value = float(cost) * (1 if name.startswith(("fapi", "dapi")) else 5)
        if "Private" in name or name.startswith(("private", "sapi")):
            value = max(value, 20)
        return max(1.0, value)

    def on_response(code, reason, url, method, headers, body, request_headers, request_body):
        try:
            guard.response(code, headers, body)
        except (GuardStorageUnavailable, sqlite3.Error):
            # The exchange may already have accepted an order. Preserve its
            # response instead of turning bookkeeping failure into a write retry.
            # Local ban deadlines survive; the next admission must access SQLite.
            logger.error("Shared Binance response bookkeeping failed; preserving exchange response")
        return original_response(
            code, reason, url, method, headers, body, request_headers, request_body
        )

    def release_ticket():
        try:
            guard.release(ticket.get())
        except (GuardStorageUnavailable, sqlite3.Error):
            # Leases expire after 45s. Cleanup must not replace a received order
            # response or mask an ambiguous write timeout.
            logger.error("Shared Binance lease cleanup failed; lease will expire")
        finally:
            ticket.set(None)

    def finish_cache(key, owner, value, ttl):
        try:
            guard.cache_finish(key, owner, value, ttl)
        except (GuardStorageUnavailable, sqlite3.Error):
            logger.error("Shared Binance cache write failed; fresh response remains usable")

    if sync:

        def throttle(cost=None):
            original_throttle(cost)
            ticket.set(guard.acquire(weight.get()))

        def fetch(url, method="GET", headers=None, body=None):
            guard.before_transport()
            return original_fetch(url, method, headers, body)

        def fetch2(path, family="public", method="GET", params=None, config=None):
            params, config = params or {}, config or {}
            token = weight.set(context(path, family, method, params, config))
            lease = ticket.set(None)
            key, ttl = cache_identity(path, family, method, params, api.urls.get("api"))
            owner = uuid.uuid4().hex
            value = None
            claimed = False
            try:
                while delay := guard.cooldown_delay():
                    time.sleep(min(delay, 30))
                if key:
                    while True:
                        state, cached = guard.cache_claim(key, owner, time.time())
                        if state == "hit":
                            return cached
                        if state == "owner":
                            claimed = True
                            break
                        time.sleep(1)
                transport_params = (
                    {**params, "maxRetriesOnFailure": 0} if isinstance(params, dict) else params
                )
                for attempt in range(len(GET_RETRY_DELAYS) + 1):
                    try:
                        value = original_fetch2(path, family, method, transport_params, config)
                        return value
                    except ccxt.NetworkError as error:
                        if not retry_read(error, method, attempt):
                            raise
                        logger.warning(
                            "Binance GET transport failed (%s); retry %s/%s",
                            type(error).__name__,
                            attempt + 1,
                            len(GET_RETRY_DELAYS),
                        )
                    finally:
                        release_ticket()
                    time.sleep(GET_RETRY_DELAYS[attempt])
            finally:
                if claimed:
                    finish_cache(key, owner, value, ttl)
                ticket.reset(lease)
                weight.reset(token)
    else:

        async def throttle(cost=None):
            await original_throttle(cost)
            ticket.set(await guard.acquire_async(weight.get()))

        async def fetch(url, method="GET", headers=None, body=None):
            guard.before_transport()
            return await original_fetch(url, method, headers, body)

        async def fetch2(path, family="public", method="GET", params=None, config=None):
            params, config = params or {}, config or {}
            token = weight.set(context(path, family, method, params, config))
            lease = ticket.set(None)
            key, ttl = cache_identity(path, family, method, params, api.urls.get("api"))
            owner = uuid.uuid4().hex
            value = None
            claimed = False
            try:
                # The deadline is cross-process SQLite state, not a local Event.
                while delay := guard.cooldown_delay():  # noqa: ASYNC110
                    await asyncio.sleep(min(delay, 30))
                if key:
                    while True:
                        state, cached = guard.cache_claim(key, owner, time.time())
                        if state == "hit":
                            return cached
                        if state == "owner":
                            claimed = True
                            break
                        await asyncio.sleep(1)
                transport_params = (
                    {**params, "maxRetriesOnFailure": 0} if isinstance(params, dict) else params
                )
                for attempt in range(len(GET_RETRY_DELAYS) + 1):
                    try:
                        value = await original_fetch2(
                            path, family, method, transport_params, config
                        )
                        return value
                    except ccxt.NetworkError as error:
                        if not retry_read(error, method, attempt):
                            raise
                        logger.warning(
                            "Binance GET transport failed (%s); retry %s/%s",
                            type(error).__name__,
                            attempt + 1,
                            len(GET_RETRY_DELAYS),
                        )
                    finally:
                        release_ticket()
                    await asyncio.sleep(GET_RETRY_DELAYS[attempt])
            finally:
                if claimed:
                    finish_cache(key, owner, value, ttl)
                ticket.reset(lease)
                weight.reset(token)

    api.throttle = throttle
    api.fetch2 = fetch2
    api.fetch = fetch
    api.on_rest_response = on_response
    api._shared_request_guard_installed = True
    logger.info(
        "Shared Binance request guard enabled: persistent cooldown, shared budget/public cache; "
        "recovery-v1 bounded storage/GET retries"
    )


class GuardedSession(HTTPGuardedSession):
    """Compatibility facade retaining native CCXT exception types and factories."""

    rate_error = ccxt.DDoSProtection

    def make_guard(self):
        return configured_guard()
