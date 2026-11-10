"""Run the actual native Backtesting lifecycle against the frozen live cohort."""

import argparse
import hashlib
import inspect
import json
import logging
import os
import socket
import threading
import time
import traceback
from pathlib import Path

import ccxt
import pandas as pd
import psutil

from freqtrade.exchange.request_guard import install
from research.crypto.nfi_live_window_20261110.prepare import END, HERE, OUT, ROOT, START, save, sha


def metadata(market, account):
    target = OUT / market / "metadata.json"
    if target.exists():
        return target
    reference = account["metadata"]
    assert sha(reference["path"]) == reference["sha256"]
    base = json.loads(Path(reference["path"]).read_text())
    client = ccxt.binance({"enableRateLimit": True, "httpsProxy": "http://127.0.0.1:7897",
                          "timeout": 25000, "apiKey": "", "secret": ""})
    install(client, sync=True)
    raw = client.publicGetExchangeInfo() if market == "spot" else client.fapiPublicGetExchangeInfo()
    save(OUT / market / "exchange-info.json", raw)
    by_id = {x["symbol"]: x for x in raw["symbols"]}
    for pair in account["pairs"]:
        old = base["markets"][pair]
        current = client.parse_market(by_id[old["id"]])
        if current["symbol"] != pair or current["id"] != old["id"]:
            raise ValueError("Market identity differs")
        base["markets"][pair] = current
        if market == "futures" and pair not in base["leverage_tiers"]:
            raise ValueError("Missing leverage brackets")
    base["source_scope"] = "Current public symbol rules; frozen existing leverage brackets; modeled fees"
    save(target, base)
    return target


def run(market):
    plan = json.loads((OUT / "plan.json").read_text())
    account = plan["accounts"][market]
    receipts = [OUT / market / (p.replace("/", "_").replace(":", "_") + "-admission.json")
                for p in account["pairs"]]
    if not all(p.exists() for p in receipts):
        raise ValueError("Account input preparation incomplete")
    for p in receipts:
        for name, digest in json.loads(p.read_text())["files"].items():
            if sha(OUT / name) != digest:
                raise ValueError("Admitted input changed")
    snapshot = metadata(market, account)
    output = OUT / market / "native-run"
    output.mkdir(exist_ok=False)
    (output / "native-results").mkdir()
    original = json.loads(Path(account["contract"]).read_text())
    raw = json.loads(json.dumps(original))
    raw.update(dry_run=True, dry_run_wallet=100, strategy="WindowReplay",
               strategy_path=str(HERE), user_data_dir=str(output),
               datadir=str(OUT / market / "candles"),
               # Native trim-then-shift drops one leading row. Include04:55 as
               # support so execution rows cover05:00..06:55, exactly24 candles.
               timerange=f"{int(START.timestamp())-300}-{int(END.timestamp())-1}",
               fee=account["cost_bps_per_side"] / 10000,
               db_url="sqlite://", export="trades", backtest_cache="none",
               exportdirectory=str(output / "native-results"),
               pairlists=[{"method": "StaticPairList"}], enable_dynamic_pairlist=False)
    raw["exchange"]["pair_whitelist"] = account["pairs"]
    if market == "futures":
        raw["daily_snapshot_file"] = str(OUT / "frozen-live-pool.json")
        raw["futures_funding_rate"] = None
    save(output / "config.json", raw)
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize import backtesting as native
    from freqtrade.resolvers import ExchangeResolver
    from deploy.mainnet_100_20261109.capital import CappedWallets

    config = setup_optimize_configuration(
        {"config": [str(output / "config.json")], "datadir": raw["datadir"],
         "exportdirectory": output / "native-results"}, RunMode.BACKTEST)
    frozen = json.loads(snapshot.read_text())

    def no_network(*args, **kwargs):
        raise RuntimeError("Offline native replay attempted network access")

    socket.socket.connect = no_network
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    exchange._markets = frozen["markets"]
    exchange._leverage_tiers = frozen["leverage_tiers"]
    exchange._api.set_markets(frozen["markets"], frozen["currencies"])
    exchange._api_async.set_markets(frozen["markets"], frozen["currencies"])
    exchange.validate_config(config)
    native.Wallets = CappedWallets
    sources = [ROOT / "freqtrade/optimize/backtesting.py",
               ROOT / "freqtrade/strategy/interface.py", ROOT / "freqtrade/wallets.py",
               HERE / "WindowReplay.py", Path(__file__),
               ROOT / "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py",
               ROOT / "deploy/nfi80_flatboost/strategy/NFIFlatTopBoostPaper.py",
               ROOT / "deploy/mainnet_100_20261109/strategy/NFIManualFlatBoost.py",
               ROOT / "deploy/mainnet_100_20261109/capital.py"]
    save(output / "registration.json", {
        "registered_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "results_observed": False, "primary_engine": "freqtrade.optimize.backtesting.Backtesting",
        "sources": {str(p.relative_to(ROOT)): sha(p) for p in sources},
        "market_metadata_sha256": sha(snapshot), "config_sha256": sha(output / "config.json"),
        "data_admissions": {str(p): sha(p) for p in receipts},
        "comparison_start": str(START), "comparison_end_exclusive": str(END),
        "simulation_only": True, "outbound_sockets_blocked": True,
    })
    started = time.monotonic()
    stop = threading.Event()

    def monitor():
        while not stop.wait(5):
            rss = psutil.Process().memory_info().rss
            save(output / "progress.json", {"elapsed_s": time.monotonic()-started, "rss": rss})
            if rss > 3 * 1024**3 or time.monotonic() - started > 1200:
                os._exit(73)

    threading.Thread(target=monitor, daemon=True).start()
    bt = native.Backtesting(config, exchange=exchange)
    bt.progress = None
    if bt.pairlists.whitelist != account["pairs"]:
        raise ValueError("Native actual pairlist differs from frozen live list")
    signals, confirmations, attempts, indicator_counts = [], [], [], []
    for strategy in bt.strategylist:
        original_signals = strategy.ft_advise_signals
        original_confirm = strategy.confirm_trade_entry
        original_advise = strategy.advise_all_indicators

        def advise(frames, operation=original_advise):
            result = {}
            for i, (pair, data) in enumerate(frames.items(), 1):
                result.update(operation({pair: data}))
                indicator_counts.append(pair)
                print(json.dumps({"stage": "native-indicators", "market": market,
                                  "completed": i, "total": len(frames)}), flush=True)
            return result

        def capture_signals(data, metadata, operation=original_signals):
            result = operation(data, metadata)
            for _, row in result.loc[
                (result.date + pd.Timedelta(minutes=5) >= START)
                & (result.date + pd.Timedelta(minutes=5) < END)
            ].iterrows():
                for side, column in (("long", "enter_long"), ("short", "enter_short")):
                    if row.get(column, 0) == 1:
                        signals.append({"pair": metadata["pair"], "side": side,
                                        "signal_candle": str(row.date),
                                        "next_open": str(row.date + pd.Timedelta(minutes=5)),
                                        "tag": row.get("enter_tag"),
                                        "exit_long": int(row.get("exit_long", 0) or 0),
                                        "exit_short": int(row.get("exit_short", 0) or 0)})
            return result

        def confirm(*args, operation=original_confirm, **kwargs):
            accepted = operation(*args, **kwargs)
            confirmations.append({"pair": kwargs["pair"], "time": str(kwargs["current_time"]),
                                  "accepted": bool(accepted), "tag": kwargs.get("entry_tag")})
            return accepted

        strategy.advise_all_indicators = advise
        strategy.ft_advise_signals = capture_signals
        strategy.confirm_trade_entry = confirm
    original_enter = bt._enter_trade

    def enter(*args, **kwargs):
        attempts.append({"pair": args[0] if args else kwargs.get("pair"),
                         "row_time": str(args[1][0]) if len(args) > 1 else None})
        return original_enter(*args, **kwargs)

    bt._enter_trade = enter
    try:
        bt.start()
        stats = bt.results["strategy"]["WindowReplay"]
        content = bt.all_bt_content["WindowReplay"]
        trades = stats["trades"]
        result = {
            "status": "NATIVE_BACKTEST_COMPLETE", "market": market,
            "start": str(START), "end_exclusive": str(END),
            "native_result": str(next((output / "native-results").glob("*.zip"))),
            "initial_equity": 100, "stake_budget": 99, "pairs": len(account["pairs"]),
            "max_open_trades": account["max_open_trades"], "leverage": account["leverage"],
            "native_pair_count": len(bt.available_pairs),
            "raw_entry_signals": signals, "entry_confirmations": confirmations,
            "native_entry_attempts": attempts,
            "native_trades": trades, "native_trade_count": len(trades),
            "native_return_pct": stats["profit_total"] * 100,
            "native_data_start": stats["backtest_start"], "native_data_end": stats["backtest_end"],
            "native_content_counters": {k: v for k, v in content.items()
                                        if k in ("rejected_signals", "timedout_entry_orders",
                                                 "canceled_trade_entries", "replaced_entry_orders")},
            "indicators_completed": len(indicator_counts),
            "elapsed_s": time.monotonic() - started,
            "outbound_sockets_blocked": True, "live_orders_submitted": 0,
        }
        save(output / "result.json", result)
        print(json.dumps({"completed": True, "market": market, "raw_signals": len(signals),
                          "entry_attempts": len(attempts), "trades": len(trades)}), flush=True)
    finally:
        stop.set()
        bt.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("market", choices=["spot", "futures"])
    args = parser.parse_args()
    run(args.market)
