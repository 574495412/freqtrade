"""Root-gated future real NFI lifecycle producer; importing this never starts one."""

import argparse
import importlib.util
import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile

from .admission import checked, read, sha, shared_worker_admission
from .capture_native import Capture, typed


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VARIANTS = ("legacy_zstd_lru4", "analyzed_blocks4096_lru32")
_ADMITTED = object()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def load(name, record):
    spec = importlib.util.spec_from_file_location(name, checked(record))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resources:
    """Bound only this future worker, with checks before native construction."""

    def __init__(self, plan, output):
        import psutil

        self.psutil, self.process = psutil, psutil.Process()
        self.limits, self.output = plan["resource_limits"], output
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.peak = 0
        self.minimum_available = self.psutil.virtual_memory().available
        self.monitor = None

    def sample(self):
        rss = self.process.memory_info().rss
        available = self.psutil.virtual_memory().available
        free = shutil.disk_usage(ROOT).free
        self.peak = max(self.peak, rss)
        self.minimum_available = min(self.minimum_available, available)
        result = {
            "worker_pid": os.getpid(),
            "rss_bytes": rss,
            "peak_sampled_rss_bytes": self.peak,
            "available_bytes": available,
            "minimum_available_bytes": self.minimum_available,
            "free_disk_bytes": free,
            "elapsed_seconds": time.monotonic() - self.started,
        }
        failed = (
            rss > self.limits["max_rss_bytes"]
            or available < self.limits["minimum_system_available_bytes"]
            or free < self.limits["minimum_free_disk_bytes"]
            or result["elapsed_seconds"] > self.limits["max_seconds_per_variant"]
        )
        return result, failed

    def begin(self):
        result, failed = self.sample()
        if failed:
            raise RuntimeError(
                "Resource floor failed before native construction: " + json.dumps(result)
            )

        def monitor():
            while not self.stop.wait(0.25):
                try:
                    result, failed = self.sample()
                    if failed:
                        save(self.output / "resource_abort.json", result)
                        os._exit(73)
                except BaseException as error:
                    save(self.output / "resource_monitor_error.json", {"error": str(error)})
                    os._exit(74)

        self.monitor = threading.Thread(target=monitor, daemon=True)
        self.monitor.start()

    def finish(self):
        self.stop.set()
        if self.monitor is not None:
            self.monitor.join(timeout=1)
        return self.sample()[0]


def _construct(plan, output, admitted):
    if admitted is not _ADMITTED:
        raise ValueError("Native constructor has no admitted worker context")
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Environment configuration overrides are forbidden")
    prior = read(checked(plan["source_plan"]))
    old_runner = Path(plan["source_plan"]["path"]).parent / "native_runner.py"
    helper = load(
        "lifecycle_original_source_verifier",
        {"path": str(old_runner), "sha256": prior["runner_sha256"]},
    )
    engine = helper.verify(prior, prior["candidates"][0])
    if str(engine) != plan["engine_source"] or prior["engine_commit"] != plan["engine_commit"]:
        raise ValueError("Wrong original official engine")
    sys.path.insert(0, str(engine))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize import backtesting as native
    from freqtrade.resolvers import ExchangeResolver

    if Path(freqtrade.__file__).resolve() != engine / "freqtrade/__init__.py":
        raise ValueError("Wrong imported native package")
    environment = read(checked(plan["environment"]))
    if any(version(name) != expected for name, expected in environment["dependencies"].items()):
        raise ValueError("Frozen runtime versions differ")
    support = output / "support"
    for relative, record in plan["research_support"].items():
        if relative not in ("research/__init__.py", "research/data.py", "research/strategies.py"):
            raise ValueError("Support cannot replace official modules")
        path = support / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked(record), path)
    freqtrade.__path__.append(str(support))
    strategies = output / "strategies"
    strategies.mkdir()
    for name, record in plan["source_files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid original source filename")
        shutil.copyfile(checked(record), strategies / name)
    raw = read(checked(plan["config_template"]))
    raw.update(
        user_data_dir=str(output),
        strategy_path=str(strategies),
        exportdirectory=str(output / "native_results"),
        backtest_cache="none",
    )
    (output / "native_results").mkdir()
    save(output / "config.json", raw)
    config = setup_optimize_configuration(
        {
            "config": [str(output / "config.json")],
            "datadir": plan["datadir"],
            "exportdirectory": output / "native_results",
        },
        RunMode.BACKTEST,
    )
    expected = {
        "dry_run_wallet": plan["capital"],
        "fee": plan["cost_bps_per_side"] / 10000,
        "max_open_trades": 6,
        "timeframe": "5m",
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 0.99,
    }
    if any(config[key] != value for key, value in expected.items()):
        raise ValueError("Native technical account contract differs")
    if (
        config["runmode"] != RunMode.BACKTEST
        or config["dry_run"] is not True
        or config["exchange"]["pair_whitelist"] != plan["pairs"]
        or any(config["exchange"].get(key) for key in ("key", "secret", "password"))
    ):
        raise ValueError("Unexpected mode, ordered universe or credentials")
    snapshot = read(checked(plan["market_snapshot"]))
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    account = None
    try:
        exchange._markets = snapshot["markets"]
        exchange._leverage_tiers = snapshot["leverage_tiers"]
        exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
        exchange.validate_config(config)
        account = native.Backtesting(config, exchange=exchange)
        account.progress = None
        if account.required_startup != 800 or account.pairlists.whitelist != plan["pairs"]:
            raise ValueError("Native startup or ordered40 differs")
    except BaseException:
        if account is not None:
            account.cleanup()
        exchange.close()
        raise
    return account, native, exchange, helper, prior


def _run_admitted(plan, variant, output, admitted):
    if admitted is not _ADMITTED:
        raise ValueError("No admitted native lifecycle context")
    resources = Resources(plan, output)
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection
    account = exchange = trace = installed = None
    native = None
    cleaned = False

    def no_network(*args, **kwargs):
        raise RuntimeError("Offline native technical replay attempted a network connection")

    socket.socket.connect = no_network
    socket.create_connection = no_network
    try:
        resources.begin()  # Before Configuration, Exchange, Backtesting or Wallets construction.
        account, native, exchange, helper, prior = _construct(plan, output, admitted)
        from freqtrade.persistence import LocalTrade, Trade
        from freqtrade.persistence.custom_data import CustomDataWrapper

        strategy = account.strategylist[0]
        provider = account.dataprovider
        native_clear = provider.clear_cache
        cache = output / "lossless_cache"
        cache.mkdir()
        adapter = load("lifecycle_original_lossless_adapter", prior["storage_adapter"])
        installed = adapter.install(account, native, prior, cache, output)
        block_stores = []
        if variant == "analyzed_blocks4096_lru32":
            from research.crypto.nfi40_frame_cache_native_20261117_v1.frame_storage_v3 import (
                install,
            )

            # Keep all raw/indicator/trim/row paths from the original adapter.
            # Only replace the private analyzed mapping and its native-clear attachment.
            provider.clear_cache = native_clear
            block_stores = install(provider, cache / "analyzed_blocks")

        def state_reader():
            target = getattr(strategy, "target_profit_cache", None)
            # cd_value contains all strategy-owned values, including simulation
            # times. DB bookkeeping created_at/updated_at is not a decision field.
            custom = [
                {
                    "trade_id": d.ft_trade_id,
                    "key": d.cd_key,
                    "type": d.cd_type,
                    "serialized_value": d.cd_value,
                }
                for d in CustomDataWrapper.custom_data
            ]
            return {
                "profit_target_data": target.data if target is not None else None,
                "custom_data": sorted(custom, key=lambda d: (d["trade_id"], d["key"])),
            }

        def wallet_reader():
            return {
                "balances": account.wallets.get_all_balances(),
                "positions": account.wallets.get_all_positions(),
                "closed_realized_pnl": LocalTrade.bt_total_profit,
                "open_trades": list(LocalTrade.bt_trades_open),
            }

        trace = Capture(output, state_reader)
        trace.install_provider(provider, variant)
        trace.install_strategy(strategy)
        trace.install_account(account, lambda: list(LocalTrade.bt_trades_open), wallet_reader)
        observer_module = load("lifecycle_original_observer", prior["observer_source"])
        observer_plan = {
            **prior,
            "initial_equity_usdt": plan["capital"],
            "observer_pairs": plan["pairs"],
            "observation_start": plan["start"],
            "observation_end": plan["end"],
        }
        observer = observer_module.Observer(account, observer_plan, output)
        capture = account._capture_wallet

        def observed(current_time, currency, price):
            result = capture(current_time, currency, price)
            if currency == "USDT" and current_time.minute == 0:
                observer.capture(current_time)
            return result

        account._capture_wallet = observed
        if list(output.glob("nfix8-profit_max-*.json")):
            raise ValueError("Cold start found a previous NFI profit-target cache")
        save(
            output / "native_start_invocation.json",
            {
                "native_method": "Backtesting.start",
                "plan_sha256": sha(HERE / "plan.json"),
                "variant": variant,
                "orders_enabled": False,
            },
        )
        trace.phase = "native_start"
        started = time.perf_counter()
        account.start()
        native_seconds = time.perf_counter() - started
        trace.fail_if_errors()
        stats = account.results["strategy"]["NostalgiaForInfinityX8"]
        if (
            LocalTrade.bt_trades_open
            or any(t["leverage"] != 1 for t in stats["trades"])
            or abs(LocalTrade.bt_total_profit - stats["profit_total_abs"]) > 1e-5
        ):
            raise ValueError("Native terminal state failed to reconcile")
        for trade in LocalTrade.bt_trades:
            trace.append("native_trade_records", {"trade": typed(trade)})
        trace.append("hourly_wallet_records", {"native_terminal_wallet": typed(wallet_reader())})
        trace.state("native_start:completed")
        save(output / "fill_cash_audit.json", helper.audit_trades(stats))
        archives = list((output / "native_results").glob("*.zip"))
        if len(archives) != 1:
            raise ValueError("Native start must produce exactly one ZIP")
        with ZipFile(archives[0]) as archive:
            if archive.testzip() is not None:
                raise ValueError("Native ZIP integrity failure")
        # Independent fill/cash/mark identities use the unchanged original observer.
        trace.append("hourly_wallet_records", {"observer_rows": typed(observer.rows)})
        for store in block_stores:
            store.flush()
        before = {"Trade_use_db": Trade.use_db, "CustomData_use_db": CustomDataWrapper.use_db}
        account.cleanup()
        cleaned = True
        after = {"Trade_use_db": Trade.use_db, "CustomData_use_db": CustomDataWrapper.use_db}
        trace.append(
            "cleanup_records", {"native_cleanup_called": True, "before": before, "after": after}
        )
        trace.phase = "post_cleanup_verification"
        provider.clear_cache()
        for pair in (plan["pairs"][0], "MISSING/USDT:USDT"):
            frame, refreshed = provider.get_analyzed_dataframe(pair, "5m")
            if not frame.empty or refreshed != datetime.fromtimestamp(0, UTC):
                raise ValueError("Native post-clear empty/epoch semantics differ")
        trace.fail_if_errors()
        helper.verify(prior, prior["candidates"][0])
        trace.close()
        contract = {
            "plan_sha256": sha(HERE / "plan.json"),
            "source_files": plan["source_files"],
            "engine_commit": plan["engine_commit"],
            "pairs": plan["pairs"],
            "start": plan["start"],
            "end": plan["end"],
            "capital": plan["capital"],
            "cost_bps": plan["cost_bps_per_side"],
            "synthetic": False,
        }
        receipt = {
            "producer_kind": "ACTUAL_OFFICIAL_BACKTESTING_LIFECYCLE",
            "cache_variant": variant,
            "worker_method_completed": True,
            "terminal_exit_code": None,
            "callback_errors": trace.errors,
            "contract": contract,
            **trace.records,
            "measurements": {
                **trace.elapsed,
                "instrumented_native_start_seconds": native_seconds,
                "block_stats": [store.stats for store in block_stores],
            },
            "native_zip": {"path": str(archives[0]), "sha256": sha(archives[0])},
            "full_semantic_trace": {
                "path": str(output / "actual_native_trace.jsonl"),
                "sha256": sha(output / "actual_native_trace.jsonl"),
            },
            "full_window_financial_parity_proven": False,
            "execution_replacement_admitted": False,
            "orders_enabled": False,
        }
        save(output / "native_lifecycle_receipt.json", receipt)
        return {"variant": variant, "receipt_sha256": sha(output / "native_lifecycle_receipt.json")}
    except BaseException as error:
        save(
            output / "failure.json",
            {
                "type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "callback_errors": trace.errors if trace else [],
                "orders_enabled": False,
            },
        )
        raise
    finally:
        if account is not None and not cleaned:
            try:
                account.cleanup()
                save(output / "failure_cleanup.json", {"native_cleanup_called": True})
            except BaseException as error:
                save(
                    output / "failure_cleanup.json",
                    {"native_cleanup_called": False, "error": str(error)},
                )
        if trace is not None:
            trace.close()
        if installed is not None and native is not None:
            native.trim_dataframes = installed["original_trim"]
        try:
            if exchange is not None:
                exchange.close()
        finally:
            socket.socket.connect = original_connect
            socket.create_connection = original_create_connection
            save(output / "resources.json", resources.finish())


def run_variant(variant):
    if variant not in VARIANTS:
        raise ValueError("Unknown frozen cache variant")
    # No native imports, constructors, output creation or resource thread precedes this gate.
    with shared_worker_admission() as plan:
        output = ROOT / "user_data/crypto_quant" / HERE.name / variant
        output.mkdir(parents=True, exist_ok=False)
        return _run_admitted(plan, variant, output, _ADMITTED)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    args = parser.parse_args()
    print(json.dumps(run_variant(args.variant)))
