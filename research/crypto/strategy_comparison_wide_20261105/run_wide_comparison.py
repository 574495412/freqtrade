"""Frozen account comparisons using the unmodified official Freqtrade backtester.

Only source/data verification, metadata loading, lossless storage and read-only
valuation are added. No order matcher, alpha, wallet or strategy policy is replaced.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


def checked(record):
    path = Path(record["path"])
    if path.is_symlink() or sha(path) != record["sha256"]:
        raise ValueError(f"Frozen input differs: {path}")
    return path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(plan, candidate):
    if sha(__file__) != plan["runner_sha256"]:
        raise ValueError("Runner differs from registration")
    engine = Path(plan["engine_source"])
    manifest = json.loads(checked(plan["engine_manifest"]).read_text())
    if manifest["commit"] != plan["engine_commit"]:
        raise ValueError("Wrong official engine commit")
    for name, digest in manifest["files"].items():
        if sha(engine / name) != digest:
            raise ValueError("Official engine changed: " + name)
    expected_py = {n for n in manifest["files"] if n.endswith(".py")}
    if {str(p.relative_to(engine)) for p in engine.rglob("*.py")} != expected_py:
        raise ValueError("Extra official engine Python source")
    receipts = json.loads(checked(plan["data_receipts"]).read_text())
    for name, digest in receipts.items():
        if sha(Path(plan["datadir"]) / name) != digest:
            raise ValueError("Historical input changed: " + name)
    for record in [candidate["config"], plan["storage_source"], plan["observer_source"],
                   plan["market_snapshot"], plan["environment"], *plan["marks"].values(),
                   *candidate["source_files"].values(), *plan["research_support"].values()]:
        checked(record)
    return engine


def audit_trades(stats):
    """Independent Decimal reconciliation from native exported fills."""
    rows = []
    total = Decimal(0)
    for trade in stats["trades"]:
        entry_qty = exit_qty = entry_cash = exit_cash = Decimal(0)
        for order in trade["orders"]:
            amount = Decimal(str(order["amount"]))
            value = amount * Decimal(str(order["safe_price"]))
            if order["ft_is_entry"]:
                entry_qty += amount
                entry_cash += value
            else:
                exit_qty += amount
                exit_cash += value
        direction = Decimal(-1 if trade["is_short"] else 1)
        reconstructed = (direction * (exit_cash - entry_cash)
                         - entry_cash * Decimal(str(trade["fee_open"]))
                         - exit_cash * Decimal(str(trade["fee_close"]))
                         + Decimal(str(trade["funding_fees"])))
        error = abs(reconstructed - Decimal(str(trade["profit_abs"])))
        if abs(entry_qty - exit_qty) > Decimal("0.00000001") or error > Decimal("0.00001"):
            raise ValueError(f"Native fill/cash reconstruction failed: {trade['pair']} {error}")
        total += reconstructed
        rows.append({"pair": trade["pair"], "open_timestamp": trade["open_timestamp"],
                     "close_timestamp": trade["close_timestamp"], "orders": len(trade["orders"]),
                     "cash_error_usdt": float(error), "reconstructed_pnl_usdt": float(reconstructed)})
    aggregate = abs(total - Decimal(str(stats["profit_total_abs"])))
    if aggregate > Decimal("0.0001"):
        raise ValueError("Native aggregate cash does not reconcile")
    return {"passed": True, "trades": rows, "aggregate_error_usdt": float(aggregate),
            "funding_scope": "native accrued funding reconciled; not authenticated exchange settlement"}


def period_metrics(frame, stats, period):
    import numpy as np
    import pandas as pd
    start, end = pd.Timestamp(period["start"]), pd.Timestamp(period["end"])
    part = frame.loc[frame.date.between(start, end)].copy()
    if part.empty or part.date.iloc[0] != start or part.date.iloc[-1] != end:
        raise ValueError("Missing common valuation boundary")
    expected = pd.date_range(start, end, freq="h")
    if not part.date.reset_index(drop=True).equals(pd.Series(expected)):
        raise ValueError("Incomplete common hourly valuation calendar")
    values = part.hourly_mark_equity.to_numpy(dtype=float)
    if not np.isfinite(values).all() or values[0] <= 0:
        raise ValueError("Invalid common marked account")
    dd = 1 - values / np.maximum.accumulate(values)
    daily = part.set_index("date").hourly_mark_equity.resample("1D").last()
    daily_returns = daily.pct_change().dropna()
    deviation = daily_returns.std(ddof=1)
    # Snapshots precede orders at exactly the boundary. Include completed trades
    # strictly before the endpoint, and never relabel native forced exits as natural.
    completed = [t for t in stats["trades"]
                 if start.timestamp()*1000 <= t["close_timestamp"] < end.timestamp()*1000
                 and t["exit_reason"] != "force_exit"]
    gain = math.fsum(max(t["profit_abs"], 0) for t in completed)
    loss = -math.fsum(min(t["profit_abs"], 0) for t in completed)
    return {"name": period["name"], "start": str(start), "end": str(end),
            "net_return": float(values[-1]/values[0]-1), "initial_marked_equity": float(values[0]),
            "ending_marked_equity": float(values[-1]), "hourly_mark_drawdown": float(dd.max()),
            "daily_nav_sharpe": float(daily_returns.mean()/deviation*np.sqrt(365)) if deviation > 0 else None,
            "completed_campaigns": len(completed), "campaign_profit_factor": gain/loss if loss else None,
            "campaign_profit_factor_status": "FINITE" if loss else "NO_LOSS_OBSERVED",
            "campaign_gross_profit": gain, "campaign_gross_loss": loss,
            "open_campaigns_at_end": int(part.open_trades.iloc[-1]),
            "calendar_hours": len(part), "equity_insolvent": bool((values <= 0).any())}


def run(plan_path, name, cost, output):
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Research rejects FREQTRADE environment overrides")
    plan = json.loads(plan_path.read_text())
    candidate = next(c for c in plan["candidates"] if c["name"] == name)
    if cost not in plan["total_cost_bps_per_side"]:
        raise ValueError("Unregistered cost")
    engine_root = verify(plan, candidate)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "registered_plan.json", plan)
    sys.dont_write_bytecode = True
    os.environ["GIT_CEILING_DIRECTORIES"] = str(engine_root.parent)
    sys.path.insert(0, str(engine_root))
    import freqtrade
    import pandas as pd
    import psutil
    from importlib.metadata import version
    if Path(freqtrade.__file__).resolve() != engine_root / "freqtrade/__init__.py":
        raise ValueError("Wrong Freqtrade package selected")
    environment = json.loads(checked(plan["environment"]).read_text())
    if any(version(n) != v for n, v in environment["dependencies"].items()):
        raise ValueError("Frozen dependency version changed")
    support = output / "support"
    for relative, record in plan["research_support"].items():
        if relative not in ("research/__init__.py", "research/data.py", "research/strategies.py"):
            raise ValueError("Support package cannot replace official core modules")
        target = support / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked(record), target)
    freqtrade.__path__.append(str(support))
    strategies = output / "strategies"
    strategies.mkdir()
    for relative, record in candidate["source_files"].items():
        if Path(relative).name != relative:
            raise ValueError("Invalid source filename")
        shutil.copyfile(checked(record), strategies / relative)
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize import backtesting as native
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver
    raw = json.loads(checked(candidate["config"]).read_text())
    raw.update(datadir=plan["datadir"], user_data_dir=str(output), strategy_path=str(strategies),
               timerange=f"{int(pd.Timestamp(plan['start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp())-1}",
               fee=cost/10000, exportdirectory=str(output / "native_results"), db_url="sqlite://")
    (output / "native_results").mkdir()
    save(output / "config.json", raw)
    config = setup_optimize_configuration({"config": [str(output / "config.json")],
                                           "datadir": plan["datadir"],
                                           "exportdirectory": output / "native_results"}, RunMode.BACKTEST)
    if (config["runmode"] != RunMode.BACKTEST or not config["dry_run"]
            or config["dry_run_wallet"] != plan["initial_equity_usdt"]
            or config["exchange"]["pair_whitelist"] != plan["pairs"]
            or Path(config["datadir"]).resolve() != Path(plan["datadir"]).resolve()
            or any(config["exchange"].get(k) for k in ("key", "secret", "password"))):
        raise ValueError("Unexpected research account contract")
    frozen_markets = json.loads(checked(plan["market_snapshot"]).read_text())
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    exchange._markets = frozen_markets["markets"]
    exchange._leverage_tiers = frozen_markets["leverage_tiers"]
    exchange._api.set_markets(frozen_markets["markets"], frozen_markets["currencies"])
    exchange._api_async.set_markets(frozen_markets["markets"], frozen_markets["currencies"])
    exchange.validate_config(config)
    # All required public metadata and candles are now frozen. No network or
    # account transport is allowed during the actual financial replay.
    import socket
    def no_network(*args, **kwargs):
        raise RuntimeError("Frozen historical replay attempted network access")
    socket.socket.connect = no_network
    started = time.monotonic()
    finished = threading.Event()
    def monitor():
        process = psutil.Process()
        maximum = 0
        while not finished.wait(5):
            rss = process.memory_info().rss
            maximum = max(maximum, rss)
            state = {"pid": os.getpid(), "elapsed_seconds": time.monotonic()-started,
                     "rss_bytes": rss, "maximum_rss_bytes": maximum}
            save(output / "progress.json", state)
            if rss > plan["memory_guard_bytes"] or state["elapsed_seconds"] > plan["wall_guard_seconds"]:
                save(output / "resource_guard.json", state)
                os._exit(73)
    threading.Thread(target=monitor, daemon=True).start()
    storage = load_module("frozen_lossless_storage", checked(plan["storage_source"]))
    observer_module = load_module("frozen_cash_observer", checked(plan["observer_source"]))
    cache = output / "lossless_cache"
    cache.mkdir()
    original_trim = native.trim_dataframes
    serial = 0
    def trim(frames, timerange, startup_candles):
        nonlocal serial
        serial += 1
        result = storage.DiskMapping(cache / f"trim_{serial}", 1)
        for pair, frame in frames.items():
            result.update(original_trim({pair: frame}, timerange, startup_candles))
        return result
    native.trim_dataframes = trim
    account = None
    try:
        account = native.Backtesting(config, exchange=exchange)
        account.progress = None
        provider = account.dataprovider
        for strategy in account.strategylist:
            operation = strategy.advise_all_indicators
            def advise(frames, operation=operation):
                result = storage.DiskMapping(cache / "indicators", 1)
                for count, (pair, frame) in enumerate(frames.items(), 1):
                    result.update(operation({pair: frame}))
                    print(json.dumps({"stage": "indicators", "pair": pair, "completed": count}), flush=True)
                return result
            strategy.advise_all_indicators = advise
        # Provider retains at most this six-pair account's analyzed frames to
        # avoid reading multi-gigabyte frames from disk on every 5-minute tick.
        # The registered RSS guard bounds this cache without downcasting data.
        original_clear = provider.clear_cache
        def clear():
            nonlocal serial
            original_clear()
            serial += 1
            provider._DataProvider__cached_pairs = storage.DiskMapping(cache / f"analyzed_{serial}", 6)
        provider.clear_cache = clear
        clear()
        observer_plan = {**plan, "observer_pairs": plan["pairs"],
                         "observation_start": plan["start"], "observation_end": plan["end"]}
        observer = observer_module.Observer(account, observer_plan, output)
        original_capture = account._capture_wallet
        def capture(current_time, currency, price):
            original_capture(current_time, currency, price)
            if currency == "USDT" and current_time.minute == 0:
                observer.capture(current_time)
        account._capture_wallet = capture
        account.start()
        stats = account.results["strategy"][candidate["strategy"]]
        if any(t["leverage"] != plan["allowed_leverage"] for t in stats["trades"]):
            raise ValueError("Strategy used unregistered leverage")
        if LocalTrade.bt_trades_open or abs(LocalTrade.bt_total_profit-stats["profit_total_abs"]) > 1e-5:
            raise ValueError("Native terminal account did not reconcile")
        save(output / "fill_cash_audit.json", audit_trades(stats))
        frame = pd.DataFrame(observer.rows)
        initial = {"date": pd.Timestamp(plan["start"]), "equity": plan["initial_equity_usdt"],
                   "hourly_mark_equity": plan["initial_equity_usdt"], "open_trades": 0,
                   "gross": 0, "closed_realized_pnl": 0}
        if not frame.date.eq(initial["date"]).any():
            frame = pd.concat([pd.DataFrame([initial]), frame], ignore_index=True)
        frame.to_parquet(output / "hourly_equity.parquet", index=False)
        periods = [period_metrics(frame, stats, period) for period in plan["periods"]]
        save(output / "effective_config.json", config)
        result = {"candidate": name, "strategy": candidate["strategy"], "cost_bps_per_side": cost,
                  "periods": periods, "source_strategy_files": candidate["source_files"],
                  "native_terminal_return": stats["profit_total"],
                  "native_terminal_balance": stats["final_balance"],
                  "native_terminal_trade_count": len(stats["trades"]),
                  "native_terminal_exit_times": sorted({t["close_date"] for t in stats["trades"] if t["exit_reason"] == "force_exit"}),
                  "native_funding_net_usdt": math.fsum(t["funding_fees"] for t in stats["trades"]),
                  "maximum_open_cash_identity_error_usdt": observer.maximum_identity_error,
                  "maximum_native_basis_rounding_bias_usdt": observer.maximum_basis_rounding_bias,
                  "elapsed_seconds": time.monotonic()-started,
                  "engine_version": freqtrade.__version__, "engine_source": freqtrade.__file__,
                  "orders_enabled": False, "production_eligible": False,
                  "comparison_valuation": "common hourly mark OPEN before current bar processing; remaining close fee reserved; not intrahour worst case",
                  "native_terminal_not_used_for_ranking": True}
        verify(plan, candidate)
        save(output / "result.json", result)
        print(json.dumps({"candidate": name, "cost": cost, "state": "completed",
                          "periods": periods, "elapsed_seconds": result["elapsed_seconds"]}), flush=True)
        return result
    finally:
        finished.set()
        exchange.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--cost-bps", type=int, choices=[5, 8, 15], required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        run(args.plan.resolve(), args.candidate, args.cost_bps, args.output.resolve())
    except BaseException as exc:
        if args.output.exists():
            save(args.output / "failure.json", {"type": type(exc).__name__, "message": str(exc),
                                               "traceback": traceback.format_exc(), "orders_enabled": False})
        raise
