"""Profile native initialization and candle analysis without starting a trade loop.

Uses a new database and private working directory. All HTTP writes and native
order mutations are blocked in this process; the original account files are read
only. This is resource engineering evidence, not a paper or live return account.
"""

import argparse
import hashlib
import json
import os
import time
import traceback
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from deploy.aliyun_20261111.systemd import AGGREGATE_HIGH_MIB, AGGREGATE_MAX_MIB


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "user_data/deployment"


class ProbeWriteBlocked(RuntimeError):
    """A resource probe must never change an exchange account."""


@contextmanager
def readonly_transport():
    import aiohttp
    import requests
    from ccxt.async_support.base.exchange import Exchange as AsyncExchange
    from ccxt.base.exchange import Exchange as SyncExchange

    counters = {"get_calls": 0, "blocked_writes": 0}

    def check(method):
        if str(method).upper() != "GET":
            counters["blocked_writes"] += 1
            raise ProbeWriteBlocked("Non-GET transport disabled in resource probe")
        counters["get_calls"] += 1

    request = requests.Session.request
    aio_request = aiohttp.ClientSession._request
    sync_fetch, async_fetch = SyncExchange.fetch, AsyncExchange.fetch

    def guarded_request(self, method, url, *args, **kwargs):
        check(method)
        return request(self, method, url, *args, **kwargs)

    async def guarded_aio_request(self, method, url, *args, **kwargs):
        check(method)
        return await aio_request(self, method, url, *args, **kwargs)

    def guarded_fetch(self, url, method="GET", *args, **kwargs):
        check(method)
        return sync_fetch(self, url, method, *args, **kwargs)

    async def guarded_async_fetch(self, url, method="GET", *args, **kwargs):
        check(method)
        return await async_fetch(self, url, method, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(requests.Session, "request", guarded_request))
        stack.enter_context(patch.object(aiohttp.ClientSession, "_request", guarded_aio_request))
        stack.enter_context(patch.object(SyncExchange, "fetch", guarded_fetch))
        stack.enter_context(patch.object(AsyncExchange, "fetch", guarded_async_fetch))
        yield counters


def profile_config(live, original_directory, destination):
    if live.get("dry_run") is not False or live.get("live_capital_mode") != "available_balance":
        raise ValueError("Expected the existing available-balance live configuration")

    def relocate(value):
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, str):
            return value.replace(str(original_directory), str(destination))
        return value

    config = relocate(deepcopy(live))
    for name in ("key", "secret", "password", "apiKey", "uid"):
        config["exchange"].pop(name, None)
    for name in ("api_server", "telegram", "webhook", "external_message_consumer"):
        config[name] = {"enabled": False}
    # The native schema requires these fields even when the RPC is disabled.
    # Explicit inert values also prevent credential-overlay RPC settings leaking
    # into this isolated, non-serving process through deep merge.
    config["telegram"].update(token="", chat_id="")
    config["external_message_consumer"]["producers"] = []
    config["api_server"].update(
        listen_ip_address="127.0.0.1", listen_port=18799,
        username="", password="", jwt_secret_key="disabled-probe-" + "0" * 32,
    )
    config.update(
        initial_state="stopped", cancel_open_orders_on_exit=False,
        user_data_dir=str(destination), db_url="sqlite:///" + str(destination / "probe.sqlite"),
        # Exercise full analysis repeatedly, including within one base candle.
        process_only_new_candles=False,
    )
    return config


def require_cgroup():
    line = next(line for line in Path("/proc/self/cgroup").read_text().splitlines()
                if line.startswith("0::"))
    parts = Path(line[3:]).parts
    end = parts.index("freqtrade-live.slice") + 1
    group = Path("/sys/fs/cgroup").joinpath(*parts[1:end])
    if (
        int((group / "memory.max").read_text()) != AGGREGATE_MAX_MIB * 1024**2
        or int((group / "memory.high").read_text()) != AGGREGATE_HIGH_MIB * 1024**2
    ):
        raise ValueError("Expected the reviewed aggregate cgroup budget")


def validate_isolation(native, derived, folder):
    if (
        native["db_url"] != derived["db_url"]
        or Path(native["user_data_dir"]).resolve() != folder
        or native.get("initial_state") != "stopped"
        or native.get("cancel_open_orders_on_exit") is not False
        or any(native.get(k, {}).get("enabled") for k in (
            "api_server", "telegram", "webhook", "external_message_consumer",
        ))
    ):
        raise ValueError("Profile isolation was overridden")


def analyze_pipeline(bot, expected):
    if len(bot.active_pair_whitelist) != expected:
        raise ValueError("Original universe size changed")
    for _ in range(2):
        bot.dataprovider.refresh(
            bot.pairlists.create_pair_list(bot.active_pair_whitelist),
            bot.strategy.gather_informative_pairs(),
        )
        bot.strategy.analyze(bot.active_pair_whitelist)
        for pair in bot.active_pair_whitelist:
            frame, _ = bot.dataprovider.get_analyzed_dataframe(pair, bot.strategy.timeframe)
            raw = bot.dataprovider.ohlcv(pair, bot.strategy.timeframe, copy=False)
            if (
                len(raw) < bot.strategy.startup_candle_count
                or len(frame) != 2
                or not {"enter_long", "exit_long"} <= set(frame.columns)
            ):
                raise ValueError("Incomplete native candle/indicator analysis")


def unchanged_ledger(original, before):
    with (original / "trades.sqlite").open("rb") as ledger:
        if hashlib.file_digest(ledger, "sha256").digest() != before:
            raise ValueError("Original ledger changed during resource probe")


def hold_and_recheck(bot, expected, folder, counters, original, before, seconds):
    deadline = time.monotonic() + seconds
    rechecked = False
    while time.monotonic() < deadline:
        if not rechecked and (folder.parent / "recheck").exists():
            analyze_pipeline(bot, expected)
            unchanged_ledger(original, before)
            if counters["blocked_writes"]:
                raise ProbeWriteBlocked("Unexpected write was attempted")
            result = {
                "at_utc": datetime.now(UTC).isoformat(), "market": folder.name,
                "pairs_analyzed": expected, "full_analysis_cycles": 2,
                "concurrent_recheck_passed": True, "transport": counters,
            }
            (folder / "rechecked.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
            rechecked = True
        time.sleep(0.5)


def run(market, folder, hold_seconds=300):
    if Path.cwd().resolve() != ROOT or Path("/run/freqtrade-live-admitted").exists():
        raise ValueError("Run in the held ECS checkout")
    require_cgroup()
    folder = folder.resolve()
    if (
        not folder.is_relative_to(STATE.resolve())
        or not folder.parent.name.startswith("native-capacity-")
        or folder.name != market
    ):
        raise ValueError("Use a new isolated native-capacity directory")
    folder.mkdir(mode=0o700, exist_ok=False)
    bot = None
    with readonly_transport() as counters:
        from deploy.mainnet_100_20261109.manual import verify
        from freqtrade.configuration import Configuration
        from freqtrade.enums import RunMode
        from freqtrade.exchange import Exchange
        from freqtrade.freqtradebot import FreqtradeBot

        verify()
        original = STATE / "mainnet-100-20261109" / market
        with (original / "trades.sqlite").open("rb") as ledger:
            ledger_before = hashlib.file_digest(ledger, "sha256").digest()
        live = json.loads((original / "config.json").read_text())
        config = profile_config(live, original, folder)
        path = folder / "profile.private.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)

        def deny(*args, **kwargs):
            counters["blocked_writes"] += 1
            raise ProbeWriteBlocked("Order mutation or trade loop disabled in resource probe")

        with ExitStack() as stack:
            for method in (
                "create_order", "cancel_order", "create_stoploss", "cancel_stoploss_order",
            ):
                stack.enter_context(patch.object(Exchange, method, deny))
            for method in ("process", "startup"):
                stack.enter_context(patch.object(FreqtradeBot, method, deny))
            try:
                native = Configuration({
                    # The isolated overlay must win over every original file.
                    # Its exchange dictionary omits keys, so deep merge keeps
                    # the existing credential overlay in memory only.
                    "config": [str(original / "config.json"),
                               str(original / "credentials.json"), str(path)],
                    "command": "trade", "strategy_path": str(
                        ROOT / "deploy/mainnet_100_20261109/strategy"
                    ), "user_data_dir": str(folder),
                }, RunMode.LIVE).get_config()
                validate_isolation(native, config, folder)
                print(json.dumps({"phase": "native_initialization", "market": market}), flush=True)
                bot = FreqtradeBot(native)
                cache_measurements = {}
                original_cache = bot.dataprovider._set_cached_df

                def measured_cache(pair, timeframe, dataframe, candle_type):
                    original_cache(pair, timeframe, dataframe, candle_type)
                    stored, _ = bot.dataprovider.get_analyzed_dataframe(pair, timeframe)
                    expected_tail = dataframe.tail(2)
                    if not stored.equals(expected_tail) or not stored.dtypes.equals(
                        expected_tail.dtypes
                    ):
                        raise ValueError("Analyzed cache changed native tail values or precision")
                    cache_measurements[pair] = {
                        "full_bytes": int(dataframe.memory_usage(deep=True).sum()),
                        "stored_bytes": int(stored.memory_usage(deep=True).sum()),
                    }

                stack.enter_context(patch.object(
                    bot.dataprovider, "_set_cached_df", measured_cache,
                ))
                expected = 36 if market == "spot" else 80
                print(json.dumps({
                    "phase": "native_analysis", "market": market,
                    "heap_after_initialization": bot.strategy._last_memory_reclaim,
                }), flush=True)
                analyze_pipeline(bot, expected)
                if counters["blocked_writes"]:
                    raise ProbeWriteBlocked("Unexpected write was attempted")
                unchanged_ledger(original, ledger_before)
                result = {
                    "at_utc": datetime.now(UTC).isoformat(), "market": market,
                    "pairs_analyzed": expected, "full_analysis_cycles": 2,
                    "transport": counters, "trade_loop_started": False,
                    "original_ledger_modified": False, "resource_pipeline_passed": True,
                    "scope": "Native initialization and analysis only; no execution-loop evidence",
                    "analyzed_cache": {
                        "full_bytes": sum(r["full_bytes"] for r in cache_measurements.values()),
                        "stored_bytes": sum(r["stored_bytes"] for r in cache_measurements.values()),
                        "exact_tail_values_and_dtypes": True,
                    },
                    "heap_after_analysis": bot.strategy._last_memory_reclaim,
                }
                (folder / "ready.json").write_text(json.dumps(result, indent=2))
                print(json.dumps(result), flush=True)
                hold_and_recheck(
                    bot, expected, folder, counters, original, ledger_before, hold_seconds,
                )
                return result
            finally:
                if bot is not None:
                    bot.cleanup()


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("spot", "futures"), required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--hold-seconds", type=int, default=300, choices=range(1801))
    args = parser.parse_args()
    try:
        run(args.market, args.folder, args.hold_seconds)
    except Exception as exc:
        print(json.dumps({
            "resource_pipeline_passed": False, "type": type(exc).__name__,
            "frames": [{"file": f.filename, "line": f.lineno} for f in
                       traceback.extract_tb(exc.__traceback__)[-6:]],
        }), flush=True)
        raise SystemExit(1) from None
