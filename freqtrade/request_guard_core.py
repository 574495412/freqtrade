"""Shared SQLite request admission without importing exchange SDKs.

Same native guard state machine; error types are supplied by the CCXT facade.
"""

import asyncio
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path


logger = logging.getLogger(__name__)
STORAGE_RETRY_DELAYS = (0.25, 0.75)

class GuardStorageUnavailable(RuntimeError):
    """A shared admission transaction could not be completed."""

class SharedCooldown(RuntimeError):
    """Persistent cooldown or budget prevents an auxiliary request."""

def retry_storage(operation):
    """Retry a complete, rolled-back SQLite transaction, never a transport."""

    @wraps(operation)
    def wrapped(*args, **kwargs):
        for attempt in range(len(STORAGE_RETRY_DELAYS) + 1):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as error:
                code = getattr(error, "sqlite_errorcode", 0) & 255
                transient = code in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                    sqlite3.SQLITE_CANTOPEN,
                    sqlite3.SQLITE_IOERR,
                    sqlite3.SQLITE_FULL,
                } or str(error) in {
                    "unable to open database file",
                    "database is locked",
                    "database table is locked",
                    "disk I/O error",
                    "database or disk is full",
                }
                if not transient:
                    raise
                if attempt == len(STORAGE_RETRY_DELAYS):
                    raise args[0].storage_error(
                        "Shared Binance storage temporarily unavailable; admission blocked "
                        f"({getattr(error, 'sqlite_errorname', 'SQLITE_OPERATIONAL_ERROR')})"
                    ) from None
                logger.warning(
                    "Shared Binance storage unavailable in %s; transaction retry %s/%s",
                    operation.__name__,
                    attempt + 1,
                    len(STORAGE_RETRY_DELAYS),
                )
                time.sleep(STORAGE_RETRY_DELAYS[attempt])

    return wrapped


class SharedRequestGuard:
    storage_error = GuardStorageUnavailable
    cooldown_error = SharedCooldown

    def __init__(self, path, *, weight_per_minute=900, requests_per_minute=180, in_flight=4):
        self.path = Path(path)
        self.weight_rate = weight_per_minute / 60
        self.request_rate = requests_per_minute / 60
        self.in_flight = in_flight
        self.local_cooldown = 0
        if min(self.weight_rate, self.request_rate, in_flight) <= 0:
            raise ValueError("Invalid shared request budget")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("Shared request database may not be a symlink")
        self.initialize()
        self.path.chmod(0o600)

    @retry_storage
    def initialize(self):
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
                CREATE INDEX IF NOT EXISTS public_cache_expiry
                    ON public_cache(expires,lease_until);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @retry_storage
    def claim(self, weight, now=None):
        """Return a ticket or a local wait duration. Never reserve a future burst."""
        now = time.time() if now is None else now
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("Invalid request weight")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM budget WHERE id=1").fetchone()
            if self.local_cooldown > row["cooldown"]:
                conn.execute("UPDATE budget SET cooldown=? WHERE id=1", (self.local_cooldown,))
            delay = max(row["cooldown"], row["next_at"], self.local_cooldown) - now
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

    @retry_storage
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
                raise self.cooldown_error("Shared Binance budget/cooldown active; no request sent")
            time.sleep(min(delay, 30))

    async def acquire_async(self, weight):
        while True:
            ticket, delay = self.claim(weight)
            if ticket:
                return ticket
            await asyncio.sleep(min(delay, 30))

    @retry_storage
    def block_until(self, until, reason):
        self.local_cooldown = max(self.local_cooldown, until)
        with self.connect() as conn:
            conn.execute(
                "UPDATE budget SET cooldown=max(cooldown,?),reason=? WHERE id=1",
                (until, reason),
            )

    @retry_storage
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
        self.local_cooldown = max(self.local_cooldown, until)
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

    @retry_storage
    def before_transport(self):
        with self.connect() as conn:
            row = conn.execute("SELECT cooldown FROM budget WHERE id=1").fetchone()
            if max(row[0], self.local_cooldown) > time.time():
                # A different in-flight request may have just reported a ban.
                # Fail before transport; next attempt waits BEFORE signing.
                raise self.cooldown_error("Shared Binance cooldown active; no request sent")
            conn.execute("UPDATE budget SET transports=transports+1 WHERE id=1")

    def cooldown_delay(self):
        return max(0, max(self.status()["cooldown"], self.local_cooldown) - time.time())

    @retry_storage
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

    @retry_storage
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

    @retry_storage
    def status(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM budget WHERE id=1").fetchone())


