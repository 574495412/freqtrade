"""Scoped historical time for an explicitly single-threaded native replay.

Only module-local bindings used by the inspected native call paths are changed.
SQLAlchemy captured defaults and unrelated modules are not certified by this
clock; native Trade/Order creation must still supply their explicit source dates.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import threading

from freqtrade.persistence import Trade
from native_snapshot import OLD  # make frozen RD1 imports available
from rd1 import RiskError, StateError, aware


class NativeReplayClock:
    _active = None
    _lock = threading.Lock()
    TARGETS = {
        "freqtrade.freqtradebot": ("datetime", "dt_now"),
        "freqtrade.wallets": ("dt_now",),
        "freqtrade.persistence.trade_model": ("datetime", "dt_now"),
        "freqtrade.persistence.pairlock_middleware": ("datetime",),
        "freqtrade.strategy.interface": ("datetime", "dt_now"),
        "freqtrade.plugins.protectionmanager": ("datetime",),
    }

    def __init__(self):
        if not Trade.use_db:
            raise RiskError("Replay clock requires the isolated native database")
        self.engine = Trade.session.get_bind()

    def require(self, now):
        now = aware(now)
        if NativeReplayClock._active != (self, now) or Trade.session.get_bind() is not self.engine:
            raise StateError("Explicit matching native replay clock is not active")
        return now

    @contextmanager
    def at(self, now):
        now = aware(now)
        if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
            raise StateError("Native replay clock requires a dedicated single-threaded process")
        if not self._lock.acquire(blocking=False):
            raise StateError("Nested or concurrent native replay clocks forbidden")
        saved = []
        try:
            if not Trade.use_db or Trade.session.get_bind() is not self.engine:
                raise RiskError("Replay clock native database changed")
            class EventDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return now.replace(tzinfo=None) if tz is None else now.astimezone(tz)

                @classmethod
                def utcnow(cls):
                    return now.astimezone(timezone.utc).replace(tzinfo=None)

            for module_name, names in self.TARGETS.items():
                module = importlib.import_module(module_name)
                for name in names:
                    saved.append((module, name, getattr(module, name)))
                    setattr(module, name, EventDatetime if name == "datetime" else lambda: now)
            NativeReplayClock._active = (self, now)
            yield
        finally:
            NativeReplayClock._active = None
            for module, name, original in reversed(saved):
                setattr(module, name, original)
            self._lock.release()
