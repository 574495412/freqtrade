"""Opt-in, cross-process Binance transport budget and persistent IP cooldown.

Local extension of the native exchange initializer. No signals, positions or
account simulation live here. All Binance hosts using this deployment share one
conservative budget; restarting a client cannot clear a server-imposed ban.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import ccxt
import requests


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "user_data/deployment/binance-request-guard.json"
PUBLIC_FAMILIES = {"public", "fapiPublic", "fapiPublicV2", "dapiPublic"}
CACHE_PATHS = {"exchangeInfo", "klines", "markPriceKlines", "indexPriceKlines"}


class SharedRequestGuard:
    def __init__(self, path, *, weight_per_minute=900, requests_per_minute=180, in_flight=4):
        self.path = Path(path)
        self.weight_rate = weight_per_minute / 60
        self.request_rate = requests_per_minute / 60
        self.in_flight = in_flight
        if min(self.weight_rate, self.request_rate, in_flight) <= 0:
            raise ValueError("Invalid shared request budget")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Shared request database may not be a symlink")
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS budget(
                    id INTEGER PRIMARY KEY CHECK(id=1),next_at REAL NOT NULL DEFAULT 0,
                    cooldown REAL NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',
                    strikes INTEGER NOT NULL DEFAULT 0,transports INTEGER NOT NULL DEFAULT 0,
                    responses INTEGER NOT NULL DEFAULT 0,cache_hits INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO budget(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS leases(ticket TEXT PRIMARY KEY,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS public_cache(
                    key TEXT PRIMARY KEY,value TEXT,expires REAL NOT NULL DEFAULT 0,
                    owner TEXT,lease_until REAL NOT NULL DEFAULT 0);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def claim(self, weight, now=None):
        """Return a ticket or a local wait duration. Never reserve a future burst."""
        now = time.time() if now is None else now
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("Invalid request weight")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM budget WHERE id=1").fetchone()
            delay = max(row["cooldown"], row["next_at"]) - now
            if delay > 0:
                return None, delay
            conn.execute("DELETE FROM leases WHERE expires<=?", (now,))
            if conn.execute("SELECT count(*) FROM leases").fetchone()[0] >= self.in_flight:
                return None, 0.2
            ticket = uuid.uuid4().hex
            conn.execute("INSERT INTO leases VALUES(?,?)", (ticket, now + 45))
            gap = max(weight / self.weight_rate, 1 / self.request_rate)
            conn.execute("UPDATE budget SET next_at=? WHERE id=1", (now + gap,))
            return ticket, 0

    def release(self, ticket):
        if ticket:
            with self.connect() as conn:
                conn.execute("DELETE FROM leases WHERE ticket=?", (ticket,))

    def acquire(self, weight, *, max_wait=None):
        started = time.monotonic()
        while True:
            ticket, delay = self.claim(weight)
            if ticket:
                return ticket
            if max_wait is not None and time.monotonic() - started + delay > max_wait:
                raise ccxt.DDoSProtection("Shared Binance budget/cooldown active; no request sent")
            time.sleep(min(delay, 30))

    async def acquire_async(self, weight):
        while True:
            ticket, delay = self.claim(weight)
            if ticket:
                return ticket
            await asyncio.sleep(min(delay, 30))

    def block_until(self, until, reason):
        with self.connect() as conn:
            conn.execute(
                "UPDATE budget SET cooldown=max(cooldown,?),reason=? WHERE id=1",
                (until, reason),
            )

    def response(self, code, headers, body, now=None):
        now = time.time() if now is None else now
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        until = 0
        reason = ""
        if code in (418, 429):
            match = re.search(r"banned until (\d{13})", str(body))
            if match:
                until = int(match[1]) / 1000
            retry = headers.get("retry-after")
            if retry:
                try:
                    until = max(until, now + float(retry))
                except ValueError:
                    try:
                        until = max(until, parsedate_to_datetime(retry).timestamp())
                    except (ValueError, TypeError, OverflowError):
                        pass
            if until <= now:
                # Missing deadlines are not permission for an immediate retry.
                until = now + (900 if code == 418 else 120)
            until += 5
            reason = f"HTTP_{code}"
        weight = headers.get("x-mbx-used-weight-1m")
        try:
            high_weight = float(weight) >= self.weight_rate * 60 if weight else False
        except ValueError:
            high_weight = False
        if high_weight and until == 0:
            until = (int(now // 60) + 1) * 60 + 5
            reason = "SHARED_IP_WEIGHT_HEADROOM"
        with self.connect() as conn:
            conn.execute("UPDATE budget SET responses=responses+1 WHERE id=1")
            if until:
                conn.execute(
                    """UPDATE budget SET cooldown=max(cooldown,?),reason=?,
                       strikes=strikes+? WHERE id=1""",
                    (until, reason, int(code in (418, 429))),
                )
        if until:
            logger.warning(
                "Shared Binance request cooldown until %s (%s); no retry before deadline",
                datetime.fromtimestamp(until, UTC).isoformat(),
                reason,
            )

    def before_transport(self):
        with self.connect() as conn:
            row = conn.execute("SELECT cooldown FROM budget WHERE id=1").fetchone()
            if row[0] > time.time():
                # A different in-flight request may have just reported a ban.
                # Fail before transport; next attempt waits BEFORE signing.
                raise ccxt.DDoSProtection("Shared Binance cooldown active; no request sent")
            conn.execute("UPDATE budget SET transports=transports+1 WHERE id=1")

    def cooldown_delay(self):
        return max(0, self.status()["cooldown"] - time.time())

    def cache_claim(self, key, owner, now):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM public_cache WHERE key=?", (key,)).fetchone()
            if row and row["expires"] > now and row["value"] is not None:
                conn.execute("UPDATE budget SET cache_hits=cache_hits+1 WHERE id=1")
                return "hit", json.loads(row["value"])
            if row and row["owner"] and row["lease_until"] > now:
                return "wait", None
            conn.execute(
                """INSERT INTO public_cache(key,owner,lease_until) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET owner=excluded.owner,
                   lease_until=excluded.lease_until""",
                (key, owner, now + 120),
            )
            return "owner", None

    def cache_finish(self, key, owner, value, ttl):
        serialized = json.dumps(value) if value is not None else None
        if serialized and len(serialized) > 8_000_000:
            serialized = None
        with self.connect() as conn:
            conn.execute(
                """UPDATE public_cache SET value=?,expires=?,owner=NULL,lease_until=0
                   WHERE key=? AND owner=?""",
                (serialized, time.time() + ttl if serialized else 0, key, owner),
            )
            conn.execute(
                "DELETE FROM public_cache WHERE expires<? AND lease_until<?",
                (time.time() - 3600, time.time()),
            )

    def status(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM budget WHERE id=1").fetchone())


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
        guard.response(code, headers, body)
        return original_response(
            code, reason, url, method, headers, body, request_headers, request_body
        )

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
                value = original_fetch2(path, family, method, transport_params, config)
                return value
            finally:
                guard.release(ticket.get())
                if claimed:
                    guard.cache_finish(key, owner, value, ttl)
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
                value = await original_fetch2(path, family, method, transport_params, config)
                return value
            finally:
                guard.release(ticket.get())
                if claimed:
                    guard.cache_finish(key, owner, value, ttl)
                ticket.reset(lease)
                weight.reset(token)

    api.throttle = throttle
    api.fetch2 = fetch2
    api.fetch = fetch
    api.on_rest_response = on_response
    api._shared_request_guard_installed = True
    logger.info(
        "Shared Binance request guard enabled: persistent cooldown, shared budget/public cache"
    )


class GuardedSession(requests.Session):
    """Guard auxiliary GET readers too; never admits a Binance trading write.

    Diagnostic waits are bounded to two seconds because their caller may already
    have signed the URL. Scheduled public input preparation can opt into waiting.
    """

    def __init__(self, *, max_wait=2):
        super().__init__()
        self.guard = configured_guard()
        self.guard_max_wait = max_wait

    def request(self, method, url, **kwargs):
        parsed = urlparse(url)
        if not self.guard or parsed.hostname not in {
            "api.binance.com",
            "fapi.binance.com",
            "dapi.binance.com",
        }:
            return super().request(method, url, **kwargs)
        if method.upper() != "GET":
            raise requests.RequestException("Auxiliary Binance session permits GET only")
        endpoint = parsed.path.rsplit("/", 1)[-1]
        weight = {
            "time": 1,
            "exchangeInfo": 20,
            "account": 20,
            "openOrders": 80,
            "apiRestrictions": 4,
            "myTrades": 20,
            "order": 4,
        }.get(endpoint, 80)
        if endpoint == "klines":
            if parsed.hostname == "api.binance.com":
                weight = 2
            else:
                limit = int((kwargs.get("params") or {}).get("limit", 500))
                weight = 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
        try:
            ticket = self.guard.acquire(weight, max_wait=self.guard_max_wait)
        except ccxt.DDoSProtection:
            raise requests.RequestException(
                "Shared Binance request cooldown/budget active"
            ) from None
        try:
            self.guard.before_transport()
            response = super().request(method, url, **kwargs)
            self.guard.response(response.status_code, response.headers, response.text)
            return response
        except ccxt.DDoSProtection:
            raise requests.RequestException("Shared Binance request cooldown active") from None
        finally:
            self.guard.release(ticket)
