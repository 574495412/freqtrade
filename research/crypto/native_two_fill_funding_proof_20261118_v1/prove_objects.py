"""Object-level funding mechanics only; no Backtesting/Wallet/Exchange instances."""

import hashlib
import json
import math
import resource
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import pandas as pd


HERE = Path(__file__).absolute().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def guard(*args, **kwargs):
    raise AssertionError("Forbidden constructor/account/network call in object-only proof")


def main():  # noqa: C901 - enumerated detached object mechanics
    if not __debug__:
        raise RuntimeError("Assertions cannot be disabled")
    frozen = json.loads((HERE / "freeze.json").read_text())
    for path, digest in frozen["files"].items():
        assert sha(path) == digest
    protocol = json.loads((HERE / "protocol.json").read_text())
    vendor = Path(protocol["vendor_root"])
    manifest = json.loads(Path(protocol["vendor_manifest"]["path"]).read_text())
    assert len(manifest["files"]) == 595
    for relative, digest in manifest["files"].items():
        assert sha(vendor / relative) == digest, relative
    sys.path.insert(0, str(vendor))
    from ccxt import TICK_SIZE

    import freqtrade
    from freqtrade.enums import TradingMode
    from freqtrade.exchange.exchange import Exchange
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade, Order
    from freqtrade.wallets import Wallets

    assert Path(freqtrade.__file__).resolve() == (vendor / "freqtrade/__init__.py").resolve()
    for cls in (LocalTrade, Order, Backtesting, Exchange):
        module = sys.modules[cls.__module__]
        assert Path(module.__file__).resolve().is_relative_to(vendor.resolve())
    # No account-global registration methods are called, and globals must remain identical.
    native_attrs = (
        "bt_trades",
        "bt_trades_open",
        "bt_trades_open_pp",
        "bt_open_open_trade_count",
        "bt_total_profit",
    )
    global_before = {name: getattr(LocalTrade, name) for name in native_attrs}
    global_lengths = {
        name: len(value) for name, value in global_before.items() if hasattr(value, "__len__")
    }
    origin = datetime(2025, 2, 1, tzinfo=UTC)
    funding = pd.DataFrame(
        {
            "date": [origin + timedelta(minutes=r["minute"]) for r in protocol["funding"]],
            "funding_rate": [r["rate"] for r in protocol["funding"]],
        }
    )
    marks = pd.DataFrame({"date": funding.date, "open": [r["mark"] for r in protocol["funding"]]})
    # Real official dataframe preparation, not hand-built private cached columns.
    combined = Exchange.combine_funding_and_mark(funding, marks)
    exchange_fixture = SimpleNamespace()
    exchange_fixture.calculate_funding_fees = MethodType(
        Exchange.calculate_funding_fees, exchange_fixture
    )
    engine_fixture = SimpleNamespace(
        trading_mode=TradingMode.FUTURES,
        funding_fee_timeframe_secs=3600,
        exchange=exchange_fixture,
        futures_data={"FIXTURE/USDT:USDT": combined},
    )
    engine_fixture._run_funding_fees = MethodType(Backtesting._run_funding_fees, engine_fixture)
    assert not isinstance(engine_fixture, Backtesting)
    assert not isinstance(exchange_fixture, Exchange)
    results, trace = [], []
    object_counts = {"detached_LocalTrade": 0, "detached_Order": 0}
    method_calls = {
        "Backtesting._run_funding_fees": 0,
        "Order.close_bt_order": 0,
        "LocalTrade.recalc_trade_from_orders_explicit": 0,
        "LocalTrade.close": 0,
    }

    def record(case, phase, minute, trade):
        trace.append(
            {
                "case": case,
                "phase": phase,
                "minute": minute,
                "amount": trade.amount,
                "is_open": trade.is_open,
                "funding_running": trade.funding_fee_running,
                "funding_total": trade.funding_fees,
                "stored_order_funding": math.fsum(o.funding_fee or 0.0 for o in trade.orders),
            }
        )

    def run_funding(case, minute, trade, force=False):
        engine_fixture._run_funding_fees(trade, origin + timedelta(minutes=minute), force=force)
        method_calls["Backtesting._run_funding_fees"] += 1
        record(case, "_run_funding_fees(force=" + str(force) + ")", minute, trade)

    def recalc(case, minute, trade):
        trade.recalc_trade_from_orders()
        method_calls["LocalTrade.recalc_trade_from_orders_explicit"] += 1
        record(case, "_enter_trade final recalc", minute, trade)

    def order(case, minute, trade, entry):
        side = trade.entry_side if entry else trade.exit_side
        now = origin + timedelta(minutes=minute)
        item = Order(
            id=1000 + object_counts["detached_Order"],
            ft_trade_id=trade.id,
            ft_is_open=True,
            ft_pair=trade.pair,
            order_id=str(1000 + object_counts["detached_Order"]),
            symbol=trade.pair,
            ft_order_side=side,
            side=side,
            order_type="market",
            status="open",
            order_date=now,
            order_filled_date=now if entry else None,
            order_update_date=now,
            ft_price=100.0,
            price=100.0,
            average=100.0,
            amount=protocol["quantity"],
            filled=0.0,
            remaining=protocol["quantity"],
            cost=protocol["quantity"] * 100.0 * (1.0 + protocol["fee"]),
            ft_order_tag="object_fixture_entry" if entry else "object_fixture_exit",
        )
        item._trade_bt = trade
        trade.orders.append(item)
        object_counts["detached_Order"] += 1
        # _try_close_open_order funding-relevant sequence.
        item.close_bt_order(now, trade)
        method_calls["Order.close_bt_order"] += 1
        record(case, "entry.close_bt_order" if entry else "exit.close_bt_order", minute, trade)
        run_funding(case, minute, trade, force=True)
        return item

    # Fail if implementation accidentally expands from detached objects to an account.
    with (
        patch.object(Backtesting, "__init__", guard),
        patch.object(Backtesting, "start", guard),
        patch.object(Wallets, "__init__", guard),
        patch.object(Exchange, "__init__", guard),
        patch("socket.socket.connect", guard),
        patch("socket.create_connection", guard),
    ):
        for spec in protocol["cases"]:
            for short in (False, True):
                case = spec["name"] + ("_short" if short else "_long")
                entry, exit_minute = spec["entry_minute"], spec["exit_minute"]
                force_tail = spec.get("force_tail", False)
                last_loop = exit_minute + 55 if force_tail else ((exit_minute + 59) // 60 + 1) * 60
                trade = None
                snapshots = []
                for minute in range(0, last_loop + 1, 5):
                    if minute % 60 == 0:
                        native = trade.funding_fees if trade is not None else 0.0
                        # Independent finite arithmetic oracle, not native helper or audit import.
                        expected = (
                            (1 if short else -1)
                            * protocol["quantity"]
                            * math.fsum(
                                row["manual_unit"]
                                for row in protocol["funding"]
                                if entry <= row["minute"] <= exit_minute and row["minute"] < minute
                            )
                        )
                        assert math.isclose(native, expected, rel_tol=0.0, abs_tol=1e-12), (
                            case,
                            minute,
                            native,
                            expected,
                        )
                        snapshots.append({"minute": minute, "native": native, "expected": expected})
                    if minute == entry:
                        trade = LocalTrade(
                            id=900000 + object_counts["detached_LocalTrade"],
                            pair="FIXTURE/USDT:USDT",
                            open_date=origin + timedelta(minutes=entry),
                            open_rate=100.0,
                            amount=0.0,
                            amount_requested=protocol["quantity"],
                            stake_amount=protocol["quantity"] * 100.0,
                            fee_open=protocol["fee"],
                            fee_close=protocol["fee"],
                            is_short=short,
                            is_open=True,
                            leverage=1.0,
                            trading_mode=TradingMode.FUTURES,
                            amount_precision=0.01,
                            price_precision=0.01,
                            precision_mode=TICK_SIZE,
                            precision_mode_price=TICK_SIZE,
                            contract_size=1.0,
                            funding_fees=0.0,
                            funding_fee_running=0.0,
                        )
                        object_counts["detached_LocalTrade"] += 1
                        # _enter_trade adjusts initial stop before its entry order.
                        trade.adjust_stop_loss(100.0, -0.99, initial=True)
                        order(case, minute, trade, True)
                        recalc(case, minute, trade)
                    if trade is not None and trade.is_open:
                        # The same-clock _check_trade_exit refresh after initial entry is essential.
                        run_funding(case, minute, trade)
                        if minute == exit_minute and not force_tail:
                            order(case, minute, trade, False)
                            trade.close_date = origin + timedelta(minutes=minute)
                            trade.close(100.0, show_msg=False)
                            method_calls["LocalTrade.close"] += 1
                            record(case, "trade.close -> recalc(is_closing=True)", minute, trade)
                if force_tail:
                    # handle_left_open occurs after the final C+55m detail candle, but uses C.
                    assert last_loop == exit_minute + 55
                    order(case, exit_minute, trade, False)
                    trade.close_date = origin + timedelta(minutes=exit_minute)
                    trade.close(100.0, show_msg=False)
                    method_calls["LocalTrade.close"] += 1
                    record(case, "force final-base close after detail loop", exit_minute, trade)
                assert trade is not None and not trade.is_open and not trade.has_open_orders
                assert trade.nr_of_successful_entries == trade.nr_of_successful_exits == 1
                assert len(trade.orders) == 2 and all(
                    o.filled == protocol["quantity"] for o in trade.orders
                )
                expected = spec["manual_long_terminal"] * (-1 if short else 1)
                assert math.isclose(trade.funding_fees, expected, rel_tol=0.0, abs_tol=1e-12), (
                    case,
                    trade.funding_fees,
                    expected,
                )
                assert math.isclose(
                    sum(o.funding_fee or 0.0 for o in trade.orders),
                    expected,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                results.append(
                    {
                        "case": case,
                        "passed": True,
                        "entry_minute": entry,
                        "exit_minute": exit_minute,
                        "force_tail": force_tail,
                        "native_final_funding": trade.funding_fees,
                        "manual_expected": expected,
                        "hourly_snapshots": snapshots,
                    }
                )
        for name, value in global_before.items():
            assert getattr(LocalTrade, name) is value
            if name in global_lengths:
                assert len(value) == global_lengths[name]
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert peak < 1024**3
    output = {
        "passed": True,
        "cases": results,
        "case_count": len(results),
        "hourly_snapshot_assertions": sum(len(r["hourly_snapshots"]) for r in results),
        "object_counts": object_counts,
        "native_method_calls": method_calls,
        "official_files_verified": 595,
        "peak_rss_bytes_macos": peak,
        "Backtesting_instances": 0,
        "Backtesting_start_calls": 0,
        "Wallet_instances": 0,
        "Exchange_instances": 0,
        "account_global_state_unchanged": True,
        "full_native_backtest_or_matching_trajectory": False,
        "market_data_used": False,
        "financial_accounts_run": 0,
        "proof_scope": (
            "Detached real native object funding mechanics only, with registered caller "
            "sequence; no matching/wallet/liquidation/callback/market execution proof"
        ),
        "freeze_sha256": sha(HERE / "freeze.json"),
    }
    save(HERE / "native_object_trace.json", trace)
    save(HERE / "validation.json", output)
    print(json.dumps({k: v for k, v in output.items() if k != "cases"}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        save(
            HERE / "failure.json",
            {
                "exception_type": type(error).__name__,
                "message": str(error),
                "code_sha256": sha(__file__),
                "financial_accounts_run": 0,
            },
        )
        raise
