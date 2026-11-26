"""Frozen native exit-clock proof, separate from the sealed DCA proof."""

import ast
import hashlib
import json
import resource
import shutil
import socket
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd

HERE = Path(__file__).resolve().parent


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def guard(*args, **kwargs):
    raise AssertionError("Forbidden account constructor/start/wallet/network operation")


def admit_config(config, evidence_present=True):
    """Narrow certificate; changed contracts require a separate proof."""
    if not evidence_present:
        raise ValueError("Actual effective config evidence absent")
    if config.get("timeframe") != "5m" or config.get("timeframe_detail", "") != "":
        raise ValueError("Only actual 5m with native absent/empty detail is covered")
    if config.get("unfilledtimeout") != {
        "entry": 3, "exit": 2, "exit_timeout_count": 0, "unit": "minutes"
    }:
        raise ValueError("Timeout contract differs")
    if config.get("order_types", {}).get("entry") != "limit":
        raise ValueError("Entry order type differs")
    if config.get("order_types", {}).get("exit") != "limit":
        raise ValueError("Exit order type differs")
    if config.get("order_time_in_force", {}).get("entry") != "GTC":
        raise ValueError("Entry time in force differs")
    if config.get("order_time_in_force", {}).get("exit") != "GTC":
        raise ValueError("Exit time in force differs")
    if config.get("runmode") != "backtest" or config.get("dry_run") is not True:
        raise ValueError("Only the frozen backtest is covered")


def source_facts(source_path, owner_path):
    tree = ast.parse(Path(source_path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NostalgiaForInfinityX8")
    methods = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    prohibited_overrides = [n for n in ("ft_check_timed_out", "adjust_order_price", "adjust_entry_price", "adjust_exit_price", "custom_entry_price") if n in methods]
    writes = []
    sensitive = {"order_date", "order_filled_date", "order_update_date", "status", "ft_is_open", "ft_order_side"}
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.attr in sensitive:
            writes.append({"line": node.lineno, "attribute": node.attr})
    assert not prohibited_overrides and not writes
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Order" for n in ast.walk(cls))
    # Load only exact, source-owned read-only timeout functions. No class constructor or main.
    selected = [methods[name] for name in ("check_entry_timeout", "check_exit_timeout")]
    namespace = {}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
    owner = ast.parse(Path(owner_path).read_text())
    backtest = next(n for n in owner.body if isinstance(n, ast.ClassDef) and n.name == "Backtesting")
    owning = {n.name: n for n in backtest.body if isinstance(n, ast.FunctionDef)}
    loop = owning["backtest_loop"]
    calls = [(n.lineno, n.func.attr) for n in ast.walk(loop) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"manage_open_orders", "_try_close_open_order", "_check_trade_exit"}]
    assert min(n for n, name in calls if name == "manage_open_orders") < min(n for n, name in calls if name == "_try_close_open_order") < min(n for n, name in calls if name == "_check_trade_exit")
    return namespace, {
        "prohibited_overrides": prohibited_overrides, "source_order_clock_or_status_writes": writes,
        "source_Order_construction_count": 0,
        "timeout_functions": {n.name: {"line": n.lineno, "end_line": n.end_lineno, "ast_sha256": hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()} for n in selected},
        "native_ordering_calls": sorted(calls),
        "setattr_scope": "Two source setattr sites target strategy self and configuration parameters; frozen effective config has no timeout-method override keys.",
    }


def main():
    if not __debug__:
        raise RuntimeError("Assertions required")
    frozen = json.loads((HERE / "freeze.json").read_text())
    for path, digest in frozen["files"].items():
        assert sha(path) == digest, path
    protocol = json.loads((HERE / "protocol.json").read_text())
    assert shutil.disk_usage(HERE).free > 1024**3
    config = json.loads(Path(protocol["effective_config"]).read_text())
    admit_config(config)
    assert not any(k in config or k in config.get("nfi_parameters", {}) for k in ("ft_check_timed_out", "check_entry_timeout", "check_exit_timeout", "adjust_order_price"))
    vendor = Path(protocol["vendor_root"])
    manifest = json.loads(Path(protocol["vendor_manifest"]).read_text())
    assert len(manifest["files"]) == 595
    for name, digest in manifest["files"].items():
        assert sha(vendor / name) == digest, name
    ns, facts = source_facts(protocol["actual_source"], vendor / "freqtrade/optimize/backtesting.py")
    sys.path.insert(0, str(vendor))
    from ccxt import TICK_SIZE
    import freqtrade
    from freqtrade.enums import MarginMode, TradingMode
    from freqtrade.exchange.exchange import Exchange
    from freqtrade.optimize import backtesting as native
    from freqtrade.persistence import LocalTrade, Order
    from freqtrade.strategy.interface import IStrategy
    from freqtrade.wallets import Wallets
    assert Path(freqtrade.__file__).resolve() == vendor / "freqtrade/__init__.py"
    B = native.Backtesting
    origin = datetime(2025, 2, 1, 8, tzinfo=UTC)
    results, trace = [], []
    sequence = 100
    native_globals = {name: getattr(LocalTrade, name) for name in ("bt_trades", "bt_trades_open", "bt_trades_open_pp", "bt_open_open_trade_count", "bt_total_profit")}
    global_lengths = {name: len(value) for name, value in native_globals.items() if hasattr(value, "__len__")}

    def record(case, **value):
        trace.append({"case": case, **value})

    def row(time, price=100.0):
        return (pd.Timestamp(time), price, price + 1, price - 1, price, 0, 0, 0, 0, "1", None)

    def fixture(case, cfg=None):
        cfg = deepcopy(config if cfg is None else cfg)
        callbacks = []
        dp = SimpleNamespace(orderbook=lambda pair, depth: {"bids": [[100.0, 1]], "asks": [[100.0, 1]]})
        strategy = SimpleNamespace(config=cfg, dp=dp, order_types=cfg["order_types"], order_time_in_force=cfg["order_time_in_force"], stoploss=-0.99)
        strategy.ft_check_timed_out = MethodType(IStrategy.ft_check_timed_out, strategy)
        for name in ("check_entry_timeout", "check_exit_timeout"):
            strategy.__dict__[name] = MethodType(ns[name], strategy)
        strategy.adjust_order_price = MethodType(IStrategy.adjust_order_price, strategy)
        strategy.adjust_entry_price = MethodType(IStrategy.adjust_entry_price, strategy)
        strategy.adjust_exit_price = MethodType(IStrategy.adjust_exit_price, strategy)
        strategy.confirm_trade_entry = lambda **kwargs: True
        strategy.order_filled = lambda **kwargs: callbacks.append({"order_filled": kwargs["order"].order_id, "at": kwargs["current_time"].isoformat()})
        exchange = SimpleNamespace(get_pair_base_currency=lambda pair: "FIXTURE", get_contract_size=lambda pair: 1.0, get_precision_amount=lambda pair: 0.001)
        engine = SimpleNamespace(strategy=strategy, config=cfg, order_id_counter=1000, fee=0.0015, precision_mode=TICK_SIZE, margin_mode=MarginMode.ISOLATED, exchange=exchange, wallets=None, trading_mode=TradingMode.FUTURES, timeframe_td=timedelta(minutes=5), timeframe_secs=300, timedout_entry_orders=0, timedout_exit_orders=0, canceled_trade_entries=0, canceled_entry_orders=0, canceled_exit_orders=0, replaced_entry_orders=0, replaced_exit_orders=0)
        for name in ("manage_open_orders", "check_order_cancel", "check_order_replace", "handle_similar_order", "cancel_open_orders", "_get_order_filled", "_try_close_open_order", "_enter_trade", "_exit_trade", "init_backtest_detail", "_time_generator", "_time_generator_det"):
            setattr(engine, name, MethodType(getattr(B, name), engine))
        engine.get_pair_precision = lambda pair, time: (0.01, TICK_SIZE)
        engine.get_valid_entry_price_and_stake = lambda *args: (100.0, 10.0, 1.0, 0.0)
        engine._run_funding_fees = lambda trade, time, force=False: callbacks.append({"funding_hook_only": time.isoformat(), "force": force})
        engine._call_adjust_stop = lambda *args: callbacks.append({"stop_hook_only": True})
        engine.init_backtest_detail()
        assert not isinstance(engine, B) and engine.wallets is None
        return engine, strategy, callbacks

    def order(trade, time, entry=True, price=100.0, close=False):
        nonlocal sequence
        sequence += 1
        side = trade.entry_side if entry else trade.exit_side
        item = Order(id=sequence, ft_trade_id=trade.id, ft_is_open=True, ft_pair=trade.pair, order_id=str(sequence), symbol=trade.pair, ft_order_side=side, side=side, order_type="limit", status="open", order_date=time, order_filled_date=time, order_update_date=time, ft_price=price, price=price, average=price, amount=0.1, filled=0.0, remaining=0.1, cost=10.015, ft_order_tag="1")
        item._trade_bt = trade
        trade.orders.append(item)
        if close:
            item.close_bt_order(time, trade)
            trade.recalc_trade_from_orders()
        return item

    def trade(short=False, initial=True):
        nonlocal sequence
        sequence += 1
        item = LocalTrade(id=sequence, pair="FIXTURE/USDT:USDT", base_currency="FIXTURE", stake_currency="USDT", open_rate=100.0, open_date=origin - timedelta(hours=1), stake_amount=10.0, amount=0.0, amount_requested=0.1, fee_open=0.0015, fee_close=0.0015, is_open=True, enter_tag="1", timeframe=5, exchange="fixture", is_short=short, trading_mode=TradingMode.FUTURES, leverage=1.0, amount_precision=0.001, price_precision=0.01, precision_mode=TICK_SIZE, precision_mode_price=TICK_SIZE, contract_size=1.0, orders=[])
        item.adjust_stop_loss(100.0, -0.99, initial=True)
        if initial:
            order(item, origin - timedelta(hours=1), close=True)
        return item

    def passed(name, **details):
        results.append({"case": name, "passed": True, **details})

    with patch.object(B, "__init__", guard), patch.object(B, "start", guard), patch.object(Wallets, "__init__", guard), patch.object(Exchange, "__init__", guard), patch.object(socket.socket, "connect", guard), patch.object(socket, "create_connection", guard), patch.object(native, "update_liquidation_prices", lambda *args, **kwargs: None):
        for short in (False, True):
            side = "short" if short else "long"
            for tag in ("source_exit_fixture", "force_exit"):
                name = side + "_old_exit_cancel_" + tag
                e, s, calls = fixture(name)
                t = trade(short)
                pending = order(t, origin - timedelta(minutes=5), entry=False)
                pending.ft_order_tag = tag
                s.dp.orderbook = guard
                s.adjust_order_price = guard
                assert e._get_order_filled(pending.ft_price, row(origin))
                assert e.manage_open_orders(t, origin, row(origin)) is False
                assert pending not in t.orders
                assert t.select_order(t.exit_side, is_open=True) is None
                assert not e._try_close_open_order(None, t, origin, row(origin))
                assert pending.status == "open" and pending.filled == 0
                assert e.timedout_exit_orders == 1 and not calls
                passed(name, built_in_timeout_precedes_source_force_exit_exception=True)
            name = side + "_same_clock_new_final_exit"
            e, s, calls = fixture(name)
            t = trade(short)
            assert e.manage_open_orders(t, origin, row(origin)) is False
            e._exit_trade(t, row(origin), 100.0, t.amount, "source_exit_fixture")
            last = t.orders[-1]
            assert last.ft_is_open and last.order_filled_utc is None
            assert last.order_date_utc == origin
            assert e._try_close_open_order(last, t, origin, row(origin))
            assert last.order_date_utc == origin == last.order_filled_utc
            assert not last.ft_is_open and last.status == "closed"
            # _process_exit_order's LocalTrade terminal operation, no wallet or registry.
            t.close_date = origin
            t.close(last.ft_price, show_msg=False)
            assert not t.is_open and t.nr_of_successful_exits == 1
            record(name, creation=last.order_date_utc.isoformat(), fill=last.order_filled_utc.isoformat(), hooks=calls)
            passed(name, real_native_exit_trade_and_try_close=True)
            for change in (False, True):
                name = side + "_out_of_contract_exit_replacement_" + str(change)
                cfg = deepcopy(config)
                cfg["unfilledtimeout"]["exit"] = 10
                e, s, calls = fixture(name, cfg)
                t = trade(short)
                pending = order(t, origin - timedelta(minutes=5), entry=False, price=95.0)
                s.check_exit_timeout = lambda **kwargs: False
                s.adjust_order_price = lambda **kwargs: 100.0 if change else kwargs["current_order_rate"]
                e.manage_open_orders(t, origin, row(origin))
                if change:
                    assert pending not in t.orders and e.replaced_exit_orders == 1
                    replacement = t.orders[-1]
                    assert replacement.order_date_utc == origin
                    assert e._try_close_open_order(replacement, t, origin, row(origin))
                    assert replacement.order_filled_utc == origin
                else:
                    assert pending in t.orders and e.replaced_exit_orders == 0
                assert pending.order_date_utc == origin - timedelta(minutes=5)
                passed(name, actual_config_inference_allowed=False)
        for age, expired in ((120, True), (119, False), (0, False)):
            e, s, _ = fixture("exit_timeout_equality")
            t = trade()
            p = order(t, origin - timedelta(seconds=age), entry=False)
            assert s.ft_check_timed_out(t, p, origin) is expired
            passed("exit_timeout_age_seconds_" + str(age), expired=expired)
        for detail, timeout, age in (("1m", 2, 1), ("", 10, 5)):
            cfg = deepcopy(config)
            cfg["timeframe_detail"] = detail
            cfg["unfilledtimeout"]["exit"] = timeout
            e, s, _ = fixture("exit_pending_counterexample", cfg)
            t = trade()
            p = order(t, origin - timedelta(minutes=age), entry=False)
            assert e.manage_open_orders(t, origin, row(origin)) is False
            assert e._try_close_open_order(p, t, origin, row(origin))
            assert p.order_date_utc < p.order_filled_utc
            passed("exit_pending_counterexample_detail_" + (detail or "none") + "_timeout_" + str(timeout), actual_config_inference_allowed=False)
        for name, mutate, present in (
            ("missing_effective_evidence", lambda c: None, False),
            ("detail_1m_rejected", lambda c: c.update(timeframe_detail="1m"), True),
            ("detail_unknown_null_rejected", lambda c: c.update(timeframe_detail=None), True),
            ("exit_timeout10_rejected", lambda c: c["unfilledtimeout"].update(exit=10), True),
            ("timeout_missing_rejected", lambda c: c.pop("unfilledtimeout"), True),
            ("timeout_unit_rejected", lambda c: c["unfilledtimeout"].update(unit="hours"), True),
        ):
            cfg = deepcopy(config)
            mutate(cfg)
            try:
                admit_config(cfg, present)
            except ValueError:
                passed(name)
            else:
                raise AssertionError(name)

    for name, old in native_globals.items():
        assert getattr(LocalTrade, name) is old
        if name in global_lengths:
            assert len(old) == global_lengths[name]
    # Only read minified fill clocks; no return/NAV/funding numeric recomputation.
    with ZipFile(protocol["native_zip"]) as archive:
        names = [n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        assert len(names) == 1
        stats = json.loads(archive.read(names[0]))["strategy"]["NostalgiaForInfinityX8"]
    boundary = []
    fill_count = 0
    grid_records = []
    start = pd.Timestamp("2025-01-01T00:00:00Z") - pd.Timedelta(minutes=800 * 5)
    end = pd.Timestamp("2026-09-01T00:00:00Z")
    expected_dates = pd.date_range(start, end, freq="5min", inclusive="left")
    for pair in config["exchange"]["pair_whitelist"]:
        path = Path(protocol["datadir"]) / "futures" / (pair.replace("/", "_").replace(":", "_") + "-5m-futures.feather")
        dates = pd.DatetimeIndex(pd.read_feather(path, columns=["date"]).date)
        assert dates.is_monotonic_increasing and dates.is_unique
        selected = dates[(dates >= start) & (dates < end)]
        assert selected.equals(expected_dates), pair
        grid_records.append({"pair": pair, "path": str(path), "sha256": sha(path), "rows_with_startup": len(selected), "first": selected[0].isoformat(), "last": selected[-1].isoformat(), "step_seconds": 300})
    for index, t in enumerate(stats["trades"]):
        assert not t["is_open"]
        filename = t["pair"].replace("/", "_").replace(":", "_") + "-1h-funding_rate.feather"
        funding_path = Path(protocol["datadir"]) / "futures" / filename
        funding = pd.read_feather(funding_path, columns=["date"])
        slots = set(int(v.timestamp() * 1000) for v in funding.date)
        entry_number = 0
        for j, o in enumerate(t["orders"]):
            when = o["order_filled_timestamp"]
            assert when % 300000 == 0 and o["amount"] > 0
            fill_count += 1
            if not o["ft_is_entry"] and when in slots:
                assert j == len(t["orders"]) - 1, "Only final exits in this scope"
                assert t["exit_reason"] not in {"force_exit", "stop_loss", "trailing_stop_loss", "liquidation"}
                boundary.append({"trade_index": index, "pair": t["pair"], "order_index": j, "order_id": o.get("order_id"), "fill_timestamp_ms": when, "inferred_creation_timestamp_ms": when, "inference": "CONDITIONAL_NATIVE_SOURCE_CONSTRAINT", "creation_exported": "order_date" in o or "order_timestamp" in o, "funding_dates_sha256": sha(funding_path), "exit_reason": t["exit_reason"]})
    assert len(boundary) == 2
    save("input_grid_validation.json", {"schema": "actual-frozen-40pair-5m-native-input-grid.v1", "passed": True, "pair_count": len(grid_records), "pairs_in_order": config["exchange"]["pair_whitelist"], "start_with_startup": start.isoformat(), "end_exclusive": end.isoformat(), "startup_candles": 800, "all_calendar_steps_seconds": 300, "fills_examined": fill_count, "all_fills_on_5m_grid": True, "rows": grid_records, "native_zip_sha256": sha(protocol["native_zip"]), "effective_config_sha256": sha(protocol["effective_config"]), "native_history_load_or_backtesting_started": False})
    save("object_trace.json", trace)
    receipt = {
        "status": "PASSED_NARROW_NATIVE_FINAL_EXIT_CREATION_CLOCK_CONSTRAINT", "fixture_cases": results,
        "fixture_case_count": len(results), "source_facts": facts, "vendor_files_verified": 595,
        "actual_15bp_exported_fills_examined": fill_count, "actual_boundary_final_exits": boundary,
        "actual_effective_config_sha256": sha(protocol["effective_config"]), "freeze_sha256": sha(HERE / "freeze.json"),
        "no_native_globals_changed": True, "native_Backtesting_instances": 0, "native_Backtesting_start_calls": 0,
        "native_Wallet_instances": 0, "native_Exchange_instances": 0, "financial_recomputations": 0,
        "full_matching_or_wallet_proof": False, "source_custom_order_filled_executed": False,
        "omitted_side_effects": ["funding hook recorded only", "liquidation price update no-op", "after-fill stop update hook recorded only", "source order_filled economics not executed", "entry stake/precision metadata are fixture constants"],
        "conclusion": "For the fixed successful15bp native5m/no-detail configuration, exit2min timeout cancels any older pending exit before ordinary fill processing. The two observed non-risk/non-force funding-boundary final exits were therefore newly created at their fill clock under this native source constraint. Exported creation metadata remains absent; no numeric funding/NAV audit is performed.",
        "not_claimed": ["venue intrabar execution", "full-account or numerical funding audit", "8bp applicability without its own terminal config/source evidence", "changed detail or timeout contracts", "global out-of-sample performance"],
        "peak_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    save("validation.json", receipt)
    print(json.dumps({"status": receipt["status"], "cases": len(results), "boundary_final_exits": len(boundary), "fills_examined": fill_count}))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        import traceback
        save("failure.json", {"type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc(), "financial_start": False})
        raise
