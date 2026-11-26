"""Native execution of the frozen Donchian60 contract, with read-only observation."""

import argparse
import fcntl
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
import traceback
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import psutil

from research.crypto.band_trend_native_20261113_v1.observe_clock import attach
from research.crypto.head_shoulders_financial_native_20261117_v3.FundingReceipts import (
    write_funding_receipt,
)
from research.crypto.head_shoulders_financial_native_20261117_v3.native_runner import (
    audit_trades,
    checked,
    load_module,
    period_metrics,
    save,
    sha,
    verify,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = HERE.parent / "head_shoulders_financial_native_20261117_v3"
NAME = "Donchian60Tail"


def read(path):
    return json.loads(Path(path).read_text())


def verify_study():
    for path, digest in read(HERE / "freeze.json")["files"].items():
        checked({"path": path, "sha256": digest})
    preflight = read(HERE / "preflight.json")
    assert preflight["passed"] and len(preflight["pair_results"]) == 40
    assert preflight["source_sha256"] == sha(HERE / (NAME + ".py"))
    assert preflight["protocol_sha256"] == sha(HERE / "protocol.json")
    plan = read(PARENT / "plan.json")
    engine = verify(plan, plan["candidates"][0])
    assert sha(HERE / "source.pdf") == read(HERE / "protocol.json")["source"]["sha256"]
    return plan, preflight, engine


def monitor_resources(output, plan, done, started):
    process = psutil.Process()
    peak = 0
    while not done.wait(2):
        rss = process.memory_info().rss
        peak = max(rss, peak)
        state = {
            "pid": process.pid, "rss_bytes": rss, "maximum_rss_bytes": peak,
            "free_disk_bytes": shutil.disk_usage(output).free, "elapsed_seconds": time.monotonic() - started,
        }
        save(output / "progress.json", state)
        if (
            rss > plan["memory_guard_bytes"] or state["elapsed_seconds"] > plan["wall_guard_seconds"]
            or state["free_disk_bytes"] < plan["minimum_free_disk_bytes"]
        ):
            save(output / "resource_guard.json", state)
            os._exit(73)


def config_for(plan, preflight, capital, cost, output):
    candidate = next(c for c in plan["candidates"] if c["capital"] == capital and c["cost_bps"] == cost)
    config = read(checked(candidate["config"]))
    for key in list(config):
        if key.startswith("research_head_shoulders"):
            del config[key]
    config.update(
        strategy=NAME, bot_name=output.name, user_data_dir=str(output),
        strategy_path=str(output / "strategies"), exportdirectory=str(output / "native_results"),
        timerange=(
            f"{int(pd.Timestamp(plan['native_start']).timestamp())}-"
            f"{int(pd.Timestamp(plan['end']).timestamp()) - 1}"
        ),
        research_donchian60=True, research_donchian60_daily=preflight["daily_receipts"],
    )
    return config


def backtest(plan, preflight, engine, capital, cost, output):
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Environment cannot override the frozen account")
    sys.path.insert(0, str(engine))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    assert Path(freqtrade.__file__).resolve() == engine / "freqtrade/__init__.py"
    environment = read(checked(plan["environment"]))
    assert all(version(k) == v for k, v in environment["dependencies"].items())
    strategy_path = output / "strategies"
    strategy_path.mkdir()
    shutil.copyfile(HERE / (NAME + ".py"), strategy_path / (NAME + ".py"))
    (output / "native_results").mkdir()
    save(output / "config.json", config_for(plan, preflight, capital, cost, output))
    config = setup_optimize_configuration(
        {
            "config": [str(output / "config.json")],
            "datadir": plan["datadir"],
            "exportdirectory": output / "native_results",
        },
        RunMode.BACKTEST,
    )
    assert Path(config["datadir"]).resolve() == Path(plan["datadir"]).resolve()
    assert config["dry_run"] and config["runmode"] == RunMode.BACKTEST
    assert config["dry_run_wallet"] == capital and config["fee"] == cost / 10000
    assert config["exchange"]["pair_whitelist"] == plan["pairs"]
    assert not any(config["exchange"].get(k) for k in ("key", "secret", "password"))
    markets = read(checked(plan["market_snapshot"]))
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    account = None
    try:
        exchange._markets = markets["markets"]
        exchange._leverage_tiers = markets["leverage_tiers"]
        exchange._api.set_markets(markets["markets"], markets["currencies"])
        exchange._api_async.set_markets(markets["markets"], markets["currencies"])
        exchange.validate_config(config)

        def offline(*args, **kwargs):
            raise ValueError("Network disabled during the historical replay")

        socket.socket.connect = offline
        socket.create_connection = offline
        precision = load_module("dc60_common_precision", checked(plan["price_precision_source"]))

        class Account(precision.LegacyFiveMinutePrecisionMixin, Backtesting):
            def prepare_precision(self):
                # Pass the original adapter's historical-only admission flag on a
                # temporary config view. The strategy and wallet keep their own config.
                original = self.config
                try:
                    self.config = {**original, "research_head_shoulders": True}
                    self.setup_legacy_five_minute_precision(plan)
                finally:
                    self.config = original

        account = Account(config, exchange=exchange)
        account.prepare_precision()
        account.progress = None
        observe = load_module("dc60_account_observer", checked(plan["observer_source"]))
        observer = observe.Observer(account, {
            **plan, "initial_equity_usdt": capital, "observer_pairs": plan["pairs"],
            "observation_start": plan["start"], "observation_end": plan["end"],
        }, output)
        save(output / "observation_clock.json", attach(
            account, observer, plan["start"], plan["comparison_end"], capital,
        ))

        native_loop = account.backtest_loop

        def checked_loop(*args, **kwargs):
            result = native_loop(*args, **kwargs)
            if account.strategy.callback_errors:
                raise ValueError(f"Invalid strategy callback: {account.strategy.callback_errors}")
            return result

        account.backtest_loop = checked_loop
        save(output / "financial_start_invocation.json", {"method": "Backtesting.start", "orders_enabled": False})
        account.start()
        stats = account.results["strategy"][NAME]
        if account.strategy.callback_errors or LocalTrade.bt_trades_open:
            raise ValueError("Invalid strategy callbacks or unsettled native terminal inventory")
        if any(trade["leverage"] != 1 for trade in stats["trades"]):
            raise ValueError("Unexpected leverage")
        save(output / "fill_cash_audit.json", audit_trades(stats))
        save(output / "source_decisions.json", account.strategy.source_exits)
        funding = write_funding_receipt(stats, plan, output)
        frame = pd.DataFrame(observer.rows)
        frame.to_parquet(output / "hourly_equity.parquet", index=False)
        periods = [period_metrics(frame, stats, window) for window in plan["periods"]]
        archives = list((output / "native_results").glob("*.zip"))
        if len(archives) != 1:
            raise ValueError("Native ZIP missing")
        with ZipFile(archives[0]) as archive:
            if archive.testzip() is not None:
                raise ValueError("Native ZIP CRC error")
        save(output / "full_trade_metadata.json", [trade.to_json(minified=False) for trade in LocalTrade.bt_trades])
        save(output / "result.json", {
            "strategy": NAME, "candidate": output.name, "cost_bps_per_side": cost,
            "initial_equity_usdt": capital, "periods": periods, "native_trade_count": len(stats["trades"]),
            "native_terminal_balance": stats["final_balance"], "archive_sha256": sha(archives[0]),
            "hourly_curve_sha256": sha(output / "hourly_equity.parquet"),
            "conditional_old_execution_model": True, "source_callback_errors": 0,
            "complete_funding_economics": funding["complete_funding_economics"],
            "independent_market_account_audit_pending": True, "orders_enabled": False,
        })
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=int, choices=[500, 1000], required=True)
    parser.add_argument("--cost", type=int, choices=[8, 15], required=True)
    args = parser.parse_args()
    plan, preflight, engine = verify_study()
    output = ROOT / "user_data/crypto_quant" / HERE.name / f"dc60_{args.capital}_cost{args.cost}"
    with Path(plan["shared_financial_lock"]).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(ROOT).free < plan["minimum_free_disk_bytes"]:
            raise ValueError("Frozen disk floor unmet")
        output.mkdir(parents=True, exist_ok=False)
        done = threading.Event()
        started = time.monotonic()
        monitor = threading.Thread(target=monitor_resources, args=(output, plan, done, started), daemon=True)
        monitor.start()
        try:
            backtest(plan, preflight, engine, args.capital, args.cost, output)
        except BaseException:
            save(output / "failure.json", {"traceback": traceback.format_exc(), "orders_enabled": False})
            raise
        finally:
            done.set()
            monitor.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
