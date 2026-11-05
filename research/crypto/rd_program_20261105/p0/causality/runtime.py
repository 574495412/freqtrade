"""Shared offline diagnostic guards; no market replay at import time."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[5]


def environment_guard(manifest):
    record = manifest["guard_source"]
    import hashlib
    path = ROOT / record["path"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Environment guard changed")
    spec = importlib.util.spec_from_file_location("rd_causality_environment_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    snapshot = json.loads(guard.checked(manifest["environment"]).read_text())
    sys.path.insert(0, str(ROOT))
    result = guard.verify_environment(snapshot)
    return guard, result


@contextmanager
def exclusive_worker(manifest):
    path = ROOT / manifest["worker_lock"]
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install_offline_exchange(guard, snapshot_record):
    """Inject archived rules before any Backtesting ExchangeResolver call."""
    guard.install_runtime_guards()
    from freqtrade.resolvers import ExchangeResolver
    frozen = json.loads(guard.checked(snapshot_record).read_text())
    original = ExchangeResolver.load_exchange

    def load(config, *, validate=True, load_leverage_tiers=False, **kwargs):
        exchange = original(config, validate=False, load_leverage_tiers=False, **kwargs)
        exchange._markets = frozen["markets"]
        exchange._leverage_tiers = frozen["leverage_tiers"]
        exchange._api.set_markets(frozen["markets"], frozen["currencies"])
        exchange._api_async.set_markets(frozen["markets"], frozen["currencies"])
        if validate:
            exchange.validate_config(config)
        # A native reload can be requested by Backtesting; pinned rule injection
        # is sufficient, so any attempted refresh must fail under socket guards.
        return exchange

    ExchangeResolver.load_exchange = staticmethod(load)
    return lambda: setattr(ExchangeResolver, "load_exchange", staticmethod(original))


@contextmanager
def resource_watch(output, manifest):
    """Monitor process tree; preserve a terminal record before hard limit exit."""
    import psutil
    started = time.monotonic()
    stop = threading.Event()
    state = {"peak_rss_bytes": 0, "poll_seconds": 0.5,
             "max_rss_bytes": manifest["max_rss_bytes"],
             "wall_seconds_limit": manifest["wall_seconds_per_run"]}

    def monitor():
        process = psutil.Process()
        while not stop.wait(0.5):
            family = [process, *process.children(recursive=True)]
            rss = 0
            for item in family:
                try:
                    rss += item.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            state["peak_rss_bytes"] = max(state["peak_rss_bytes"], rss)
            elapsed = time.monotonic() - started
            reason = "RSS_LIMIT" if rss > manifest["max_rss_bytes"] else "WALL_LIMIT" if elapsed > manifest["wall_seconds_per_run"] else None
            if reason:
                state.update(state="RESOURCE_LIMIT", reason=reason, elapsed_seconds=elapsed,
                             exit_code=137, diagnostic_invocation_consumed=True)
                (output / "resource-usage.json").write_text(json.dumps(state, indent=2) + "\n")
                os._exit(137)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        stop.set()
        thread.join(timeout=2)
        state.update(state="FINISHED", elapsed_seconds=time.monotonic() - started)
        (output / "resource-usage.json").write_text(json.dumps(state, indent=2) + "\n")
