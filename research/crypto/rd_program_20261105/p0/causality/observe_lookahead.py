#!/usr/bin/env python3
"""Registered diagnostic wrapper; no execution unless --execute is supplied.

One official lookahead invocation per market. Each official baseline/entry/exit
backtest receives a new, empty user_data_dir. Source files and strategy signals
remain unchanged. This is an orchestration intervention, NOT an unmodified
official diagnostic and NOT a trading/backtest profitability experiment.

The wrapper takes the shared financial-worker lock and enforces RSS/wall limits.
It records every internal prepare/backtest
call, including partial failure. It never reads the deployment secret files.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from runtime import environment_guard, exclusive_worker, install_offline_exchange, resource_watch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[5]
MANIFEST = Path(__file__).with_name("diagnostic_manifest.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def verify(manifest: dict, market: str) -> dict:
    if manifest["runner_sha256"] != sha(Path(__file__)):
        raise ValueError("Diagnostic runner changed after registration")
    if sha(Path(__file__).with_name("runtime.py")) != manifest["runtime_sha256"]:
        raise ValueError("Diagnostic runtime changed after registration")
    environment_guard(manifest)
    spec = manifest["markets"][market]
    if sha(ROOT / spec["market_snapshot"]["path"]) != spec["market_snapshot"]["sha256"]:
        raise ValueError("Archived market rules changed")
    for name, digest in manifest["engine_sources"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Pinned engine file mismatch: " + name)
    if sha(ROOT / manifest["strategy_path"]) != manifest["strategy_sha256"]:
        raise ValueError("Pinned strategy mismatch")
    if sha(ROOT / spec["config_path"]) != spec["config_sha256"]:
        raise ValueError("Frozen historical diagnostic config mismatch")
    for rel, digest in spec["selected_data"].items():
        if sha(ROOT / spec["datadir"] / rel) != digest:
            raise ValueError("Frozen data mismatch: " + rel)
    config = json.loads((ROOT / spec["config_path"]).read_text())
    for key in ("key", "secret", "password", "uid", "accountId", "walletAddress", "privateKey"):
        if config.get("exchange", {}).get(key):
            raise ValueError("Diagnostic config contains credentials; refusing without printing values")
    if config.get("telegram", {}).get("token") or config.get("webhook", {}).get("url"):
        raise ValueError("Diagnostic config contains notification destination")
    if config.get("add_config_files"):
        raise ValueError("Additional config files are not allowed")
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("FREQTRADE environment override present; root must provide a clean environment")
    return config


def execute(manifest: dict, market: str, config: dict) -> int:
    spec = manifest["markets"][market]
    output = ROOT / spec["output_directory"]
    output.mkdir(parents=True, exist_ok=False)
    (output / "utility-userdir").mkdir()
    guard, environment_result = environment_guard(manifest)
    restore_exchange = install_offline_exchange(guard, spec["market_snapshot"])
    # Public historical config only. These overrides isolate side effects; they
    # are recorded and do not change signals, cost, risk or callback mode.
    sanitized = copy.deepcopy(config)
    sanitized["dry_run"] = True
    sanitized["initial_state"] = "stopped"
    for section in ("telegram", "webhook", "api_server"):
        sanitized.setdefault(section, {})["enabled"] = False
    sanitized["export"] = "none"
    sanitized["backtest_cache"] = "none"
    config_path = output / "diagnostic-config.json"
    write_json(config_path, sanitized)
    config_differences = {k: {"before": config.get(k), "after": sanitized[k]}
                          for k in sanitized if config.get(k) != sanitized[k]}

    sys.path.insert(0, str(ROOT))
    import freqtrade
    from freqtrade.main import main as freqtrade_main
    from freqtrade.optimize.analysis.lookahead import LookaheadAnalysis
    from freqtrade.resolvers import StrategyResolver

    if Path(freqtrade.__file__).resolve() != (ROOT / "freqtrade/__init__.py").resolve():
        raise ValueError("Wrong engine import root")
    expected_targets = {(x["pair"], x["open_date"]) for x in spec["alerts"]}
    records = []
    active = {}
    event_count = 0
    phase_count = 0
    backtest_count = 0
    started = time.monotonic()
    events_path = output / "callback-trace.jsonl"
    calls_path = output / "phase-calls.json"
    result_path = output / "execution.json"
    event_file = events_path.open("x")

    def emit(record):
        nonlocal event_count
        event_count += 1
        if event_count > manifest["maximum_trace_events"]:
            raise RuntimeError("Registered trace-event budget exceeded; no silent truncation")
        event_file.write(json.dumps({"sequence": event_count, "phase": active.get("id"), **record},
                                    ensure_ascii=False, allow_nan=False) + "\n")
        event_file.flush()

    def cache_snapshot(strategy, pair=None):
        cache = getattr(strategy, "target_profit_cache", None)
        data = copy.deepcopy(getattr(cache, "data", {}))
        return data.get(pair) if pair else data

    def trade_snapshot(trade):
        return {"id": trade.id, "pair": trade.pair, "open_date": iso(trade.open_date_utc),
                "is_open": trade.is_open, "is_short": trade.is_short, "amount": trade.amount,
                "enter_tag": trade.enter_tag, "funding_fees": trade.funding_fees,
                "funding_fee_running": trade.funding_fee_running,
                "orders": [{"id": o.id, "order_id": o.order_id, "side": o.ft_order_side,
                            "created_at": iso(o.order_date_utc), "filled_at": iso(o.order_filled_utc),
                            "amount": o.amount, "filled": o.filled, "price": o.safe_price,
                            "status": o.status, "funding_fee": o.funding_fee,
                            "tag": o.ft_order_tag} for o in trade.orders]}

    def is_target(trade):
        return (trade.pair, trade.open_date_utc.isoformat()) in expected_targets

    original_load = StrategyResolver.load_strategy
    original_prepare = LookaheadAnalysis.prepare_data
    original_result = LookaheadAnalysis.get_result

    def observed_result(backtesting, processed):
        nonlocal backtest_count
        backtest_count += 1
        active["engine_backtest_invoked"] = True
        return original_result(backtesting, processed)

    def observed_load(local_config):
        strategy = original_load(local_config)
        initial_cache = cache_snapshot(strategy)
        active["strategy_loads"] = active.get("strategy_loads", 0) + 1
        active["cache_at_load"] = initial_cache
        active["runmode"] = local_config["runmode"].value
        if initial_cache:
            raise ValueError("Phase cache was not empty: isolation failed")
        emit({"event": "strategy_loaded", "runmode": local_config["runmode"].value,
              "cache": initial_cache, "cache_path": str(strategy.target_profit_cache.path.relative_to(output))})
        original_exit = strategy.custom_exit
        original_order_filled = strategy.order_filled

        def observed_exit(pair, trade, current_time, current_rate, current_profit, **kwargs):
            before = cache_snapshot(strategy, pair)
            future = bool(before and datetime.fromisoformat(before["time_profit_reached"]) > current_time)
            result = original_exit(pair=pair, trade=trade, current_time=current_time,
                                   current_rate=current_rate, current_profit=current_profit, **kwargs)
            if is_target(trade) or future:
                emit({"event": "custom_exit", "time": iso(current_time), "current_rate": current_rate,
                      "current_profit": current_profit, "cache_before": before,
                      "cache_after": cache_snapshot(strategy, pair), "cache_from_future": future,
                      "result": result, "trade": trade_snapshot(trade)})
            if future:
                active["future_cache_reads"] = active.get("future_cache_reads", 0) + 1
            return result

        def observed_order_filled(pair, trade, order, current_time, **kwargs):
            before = cache_snapshot(strategy, pair)
            pre_trade = trade_snapshot(trade) if is_target(trade) else None
            result = original_order_filled(pair=pair, trade=trade, order=order,
                                           current_time=current_time, **kwargs)
            if pre_trade is not None:
                emit({"event": "order_filled", "time": iso(current_time), "order_id": order.id,
                      "recognized_as_backtest": strategy.is_backtest_mode(), "cache_before": before,
                      "cache_after": cache_snapshot(strategy, pair), "trade_before": pre_trade,
                      "trade_after": trade_snapshot(trade)})
            return result

        strategy.custom_exit = observed_exit
        strategy.order_filled = observed_order_filled
        return strategy

    def observed_prepare(analysis, holder, pairs_to_load):
        nonlocal phase_count, active
        if phase_count >= manifest["maximum_internal_backtests_per_command"]:
            raise RuntimeError("Registered internal backtest budget exceeded")
        number = phase_count
        phase_count += 1
        label = "baseline" if number == 0 else "entry" if number % 2 else "exit"
        phase_dir = output / "phase-userdirs" / f"{number:03d}-{label}"
        phase_dir.mkdir(parents=True, exist_ok=False)
        active = {"id": number, "kind": label, "pairs": list(pairs_to_load),
                  "from_dt": iso(holder.from_dt), "to_dt": iso(holder.to_dt),
                  "compared_dt": iso(getattr(holder, "compared_dt", None)),
                  "user_data_dir": str(phase_dir.relative_to(output)), "state": "STARTED",
                  "future_cache_reads": 0}
        records.append(active)
        write_json(calls_path, records)
        previous_userdir = analysis.local_config["user_data_dir"]
        analysis.local_config["user_data_dir"] = phase_dir
        try:
            original_prepare(analysis, holder, pairs_to_load)
            trades = json.loads(holder.result["results"].to_json(orient="records", date_format="iso"))
            trades_path = output / f"phase-{number:03d}-{label}-trades.json"
            write_json(trades_path, trades)
            active.update(state="COMPLETED", result_trades=len(trades),
                          trade_file=trades_path.name, trade_sha256=sha(trades_path))
            if number == 0:
                active["baseline_target_matches"] = []
                for target in spec["alerts"]:
                    matched = [t for t in trades if t["pair"] == target["pair"] and
                               datetime.fromisoformat(t["open_date"].replace("Z", "+00:00")).isoformat() == target["open_date"]]
                    active["baseline_target_matches"].append({"alert": target, "matches": matched,
                        "same_baseline_exit_time": len(matched) == 1 and datetime.fromisoformat(matched[0]["close_date"].replace("Z", "+00:00")).isoformat() == target["baseline_close_date"]})
        except BaseException as exc:
            active.update(state="FAILED", exception_type=type(exc).__name__, exception=str(exc))
            raise
        finally:
            analysis.local_config["user_data_dir"] = previous_userdir
            write_json(calls_path, records)

    StrategyResolver.load_strategy = staticmethod(observed_load)
    LookaheadAnalysis.prepare_data = observed_prepare
    LookaheadAnalysis.get_result = staticmethod(observed_result)
    command = ["lookahead-analysis", "--config", str(config_path),
               "--strategy-path", str((ROOT / manifest["strategy_path"]).parent),
               "--strategy", "NostalgiaForInfinityX8", "--datadir", str(ROOT / spec["datadir"]),
               "--userdir", str(output / "utility-userdir"), "--timerange", manifest["timerange"],
               "--pairs", *spec["pairs"], "--minimum-trade-amount", "5", "--targeted-trade-amount", "20",
               "--lookahead-analysis-exportfilename", str(output / "lookahead-result.csv")]
    evidence = {"schema_version": 1, "market": market, "state": "RUNNING", "orders_enabled": False,
                "diagnostic_only": True, "source_mutated": False, "orchestration_intervention": manifest["intervention"],
                "runner_sha256": sha(Path(__file__)), "manifest_sha256": sha(MANIFEST),
                "config_differences": config_differences, "diagnostic_config_sha256": sha(config_path),
                "command": command, "started_utc": datetime.now(UTC).isoformat(), "official_command_count": 1,
                "additional_paired_dirty_reruns": 0, "historical_comparator": spec["historical_comparator"],
                "environment_result": environment_result, "market_snapshot": spec["market_snapshot"],
                "resource_guard": "internal process-tree RSS/wall watchdog and shared nonblocking flock"}
    write_json(result_path, evidence)
    exit_code = 0
    try:
        with resource_watch(output, manifest):
            freqtrade_main(command)
        evidence["state"] = "COMPLETED"
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
        evidence["state"] = "COMPLETED" if exit_code == 0 else "FAILED"
    except BaseException as exc:
        exit_code = 1
        evidence.update(state="FAILED", exception_type=type(exc).__name__, exception=str(exc))
        raise
    finally:
        StrategyResolver.load_strategy = staticmethod(original_load)
        LookaheadAnalysis.prepare_data = original_prepare
        LookaheadAnalysis.get_result = staticmethod(original_result)
        restore_exchange()
        event_file.close()
        evidence.update(exit_code=exit_code, elapsed_seconds=time.monotonic() - started,
                        phase_prepare_attempts=phase_count,
                        actual_internal_backtests_started=backtest_count,
                        actual_internal_backtests_completed=sum(x["state"] == "COMPLETED" for x in records),
                        callback_trace_events=event_count,
                        future_cache_reads=sum(x["future_cache_reads"] for x in records),
                        phase_calls_sha256=sha(calls_path) if calls_path.exists() else None,
                        callback_trace_sha256=sha(events_path))
        csv_path = output / "lookahead-result.csv"
        evidence["official_csv_sha256"] = sha(csv_path) if csv_path.exists() else None
        evidence["engine_and_inputs_reverified"] = False
        try:
            verify(manifest, market)
            evidence["engine_and_inputs_reverified"] = True
        finally:
            write_json(result_path, evidence)
    print(json.dumps({"state": evidence["state"], "output": str(output), "internal_backtests": backtest_count,
                      "future_cache_reads": evidence["future_cache_reads"]}))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["spot", "futures"], required=True)
    parser.add_argument("--execute", action="store_true", help="Starts one registered official diagnostic; root only after commit")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    config = verify(manifest, args.market)
    if not args.execute:
        print(json.dumps({"state": "PREFLIGHT_ONLY", "market": args.market, "manifest_sha256": sha(MANIFEST),
                          "new_backtests": 0, "output_directory": manifest["markets"][args.market]["output_directory"]}))
        return 0
    with exclusive_worker(manifest):
        return execute(manifest, args.market, config)


if __name__ == "__main__":
    raise SystemExit(main())
