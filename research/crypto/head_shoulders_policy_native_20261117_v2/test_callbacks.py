"""Synthetic native callback fixtures; never start a backtest or create orders.

The records below deliberately describe interface inputs, not author-recognized
market patterns. Detached LocalTrade objects represent callback arguments only.
"""

from collections import defaultdict
from copy import deepcopy

import numpy as np
import pandas as pd

from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, ExitType, TradingMode
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.strategy import IStrategy
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper


def run_callback_fixtures(strategy_class, cfg, native_provider):  # noqa: C901 - enumerated fixture driver
    """Return a JSON receipt after exercising real callbacks without a wallet.

    Each case uses a fresh strategy and a fresh native DataProvider so that the
    caller's real strategy/provider caches remain untouched. Native trade globals
    are temporarily replaced, then their original object references are restored.
    No source recognition, market outcome, fill, order or account is produced.
    """
    if not issubclass(strategy_class, IStrategy):
        raise TypeError("An actual IStrategy subclass is required")
    if not isinstance(native_provider, DataProvider):
        raise TypeError("An actual native DataProvider is required")
    if cfg.get("freqai", {}).get("enabled", False):
        raise ValueError("Callback fixtures do not start FreqAI")
    pair = cfg["exchange"]["pair_whitelist"][0]
    origin = pd.Timestamp("2025-02-01T10:00:00Z")
    entry_time = origin + pd.Timedelta(hours=1)
    hour = pd.Timedelta(hours=1)
    native_attrs = (
        "bt_trades",
        "bt_trades_open",
        "bt_trades_open_pp",
        "bt_open_open_trade_count",
        "bt_total_profit",
    )
    original_globals = {name: getattr(LocalTrade, name) for name in native_attrs}
    original_use_db = Trade.use_db
    strategies, detached, results = [], [], []

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def record(name, **details):
        results.append({"case": name, "passed": True, **details})

    def reset_globals():
        LocalTrade.bt_trades = []
        LocalTrade.bt_trades_open = []
        LocalTrade.bt_trades_open_pp = defaultdict(list)
        LocalTrade.bt_open_open_trade_count = 0
        LocalTrade.bt_total_profit = 0.0

    def fixture(inverted=True, self_terminal=False):
        reset_globals()
        strategy = strategy_class(deepcopy(cfg))
        strategy.dp = DataProvider(strategy.config, exchange=None)
        strategy.ft_bot_start()
        strategies.append(strategy)
        event = {
            "event_id": "synthetic_callback_long" if inverted else "synthetic_callback_short",
            "pair": pair,
            "inverted": inverted,
            "side": "long" if inverted else "short",
            "break_i": 10,
            "head_width": 4,
            "source_expiry_i": 13,
            "confirmation_bar_open": origin,
            "earliest_signal_available": entry_time,
            "entry_available": entry_time,
            "expiry_bar_open": origin + 3 * hour,
            "expiry_available": origin + 4 * hour,
            "source_target_log_price": float(np.log(110.0 if inverted else 90.0)),
            "source_stop_log_price": float(np.log(90.0 if inverted else 110.0)),
            "self_terminal_at_confirmation": self_terminal,
        }
        strategy._events = {event["event_id"]: event}
        strategy._pair_events = {pair: [event]}
        trade = LocalTrade(
            id=900000 + len(detached),
            pair=pair,
            enter_tag=event["event_id"],
            open_date=entry_time.to_pydatetime(),
            open_rate=100.0,
            amount=1.0,
            stake_amount=100.0,
            fee_open=0.0015,
            fee_close=0.0015,
            is_short=not inverted,
            is_open=True,
            leverage=1.0,
            trading_mode=TradingMode.FUTURES,
            funding_fees=0.0,
        )
        detached.append(trade)
        return strategy, event, trade

    def frame(event, closes, dates=None):
        dates = dates if dates is not None else pd.date_range(origin, periods=len(closes), freq="h")
        data = pd.DataFrame({"date": dates, "close": np.asarray(closes, dtype=float)})
        for column in ("open", "high", "low"):
            data[column] = data.close
        data["volume"] = 1.0
        data["hs_event_id"] = ""
        data["hs_direction"] = 0
        data["hs_self_terminal"] = False
        match = data.date.eq(origin)
        data.loc[match, "hs_event_id"] = event["event_id"]
        data.loc[match, "hs_direction"] = 1 if event["inverted"] else -1
        data.loc[match, "hs_self_terminal"] = event["self_terminal_at_confirmation"]
        return data

    def cache(strategy, data, now):
        # Intentionally expose future rows in some cases. The real DataProvider
        # slices by index; strategy must additionally check each candle's close.
        candle_type = strategy.config.get("candle_type_def", CandleType.FUTURES)
        strategy.dp._set_cached_df(pair, "1h", data, candle_type)
        strategy.dp._set_dataframe_max_index(pair, len(data))
        strategy.dp._set_dataframe_max_date(pd.Timestamp(now).to_pydatetime())

    def confirm(strategy, event, now=entry_time, **overrides):
        arguments = {
            "pair": pair,
            "order_type": "market",
            "amount": 1.0,
            "rate": 100.0,
            "time_in_force": "GTC",
            "current_time": now,
            "entry_tag": event["event_id"],
            "side": event["side"],
        }
        arguments.update(overrides)
        return strategy_safe_wrapper(strategy.confirm_trade_entry, default_retval=True)(**arguments)

    def exit_callback(strategy, trade, now):
        return strategy_safe_wrapper(strategy.custom_exit, default_retval=False)(
            pair=pair,
            trade=trade,
            current_time=now,
            current_rate=100.0,
            current_profit=0.0,
        )

    try:
        Trade.use_db = False
        for inverted in (True, False):
            side = "long" if inverted else "short"
            cases = (
                ("target_equal", 110.0 if inverted else 90.0, None),
                ("stop_equal", 90.0 if inverted else 110.0, None),
                ("target_cross", 111.0 if inverted else 89.0, "hs_source_target"),
                ("stop_cross", 89.0 if inverted else 111.0, "hs_source_stop"),
            )
            for label, close, expected in cases:
                strategy, event, trade = fixture(inverted)
                now = entry_time + hour
                cache(strategy, frame(event, [100, close]), now)
                require(exit_callback(strategy, trade, now) == expected, f"{side}/{label}")
                require(not strategy.callback_errors, f"Unexpected callback error: {side}/{label}")
                record(f"{side}_{label}", expected_reason=expected)

            strategy, event, trade = fixture(inverted)
            cache(strategy, frame(event, [100, 100, 100, 100, 200]), entry_time)
            require(
                exit_callback(strategy, trade, entry_time) is None, "Confirmation is not expiry"
            )
            before = event["expiry_available"] - pd.Timedelta(minutes=5)
            require(exit_callback(strategy, trade, before) is None, "Expiry candle is not closed")
            require(
                exit_callback(strategy, trade, event["expiry_available"]) == "hs_source_expiry",
                "Source width-minus-one candle must expire only at its close",
            )
            require(not strategy.callback_errors, "Expiry fixture raised")
            record(f"{side}_expiry_width_minus_one_close_available")

            strategy, event, trade = fixture(inverted)
            crossing = 111.0 if inverted else 89.0
            cache(strategy, frame(event, [100, crossing]), entry_time)
            require(exit_callback(strategy, trade, entry_time) is None, "Future close leaked")
            require(
                exit_callback(strategy, trade, entry_time + pd.Timedelta(minutes=55)) is None,
                "Still-forming 1h close leaked through 5m callback",
            )
            require(
                exit_callback(strategy, trade, entry_time + hour) == "hs_source_target",
                "Newly closed 1h observation not available at the next 5m open",
            )
            require(not strategy.callback_errors, "Future-row fixture raised")
            record(f"{side}_future_rows_filtered_until_close")

            strategy, event, trade = fixture(inverted)
            cache(strategy, frame(event, [100]), entry_time)
            require(confirm(strategy, event), "Exact next-hour opening rejected")
            require(not confirm(strategy, event), "Repeated event accepted")
            require(not strategy.callback_errors, "Entry fixture raised")
            record(f"{side}_exact_next_hour_entry_and_repeat_rejection")

            strategy, event, trade = fixture(inverted)
            cache(strategy, frame(event, [100]), entry_time)
            require(
                not confirm(strategy, event, entry_time - pd.Timedelta(minutes=5)), "Early entry"
            )
            require(
                not confirm(strategy, event, entry_time + pd.Timedelta(minutes=5)), "Late entry"
            )
            require(not strategy.callback_errors, "Late-entry fixture raised")
            record(f"{side}_early_and_late_detail_entry_rejected")

            strategy, event, trade = fixture(inverted, self_terminal=True)
            data = strategy.populate_entry_trend(frame(event, [100]), {"pair": pair})
            require(data.enter_long.sum() == data.enter_short.sum() == 0, "Self-terminal signal")
            require(data.enter_tag.isna().all(), "Self-terminal entry tag survived")
            cache(strategy, data, entry_time)
            require(not confirm(strategy, event), "Self-terminal event accepted")
            record(f"{side}_self_terminal_zero_duration_mask")

        for state in ("empty", "stale", "missing_middle", "stale_after_seen"):
            strategy, event, trade = fixture()
            now = entry_time + 2 * hour
            if state == "empty":
                data = frame(event, [])
            elif state == "stale":
                data = frame(event, [100])
            elif state == "missing_middle":
                data = frame(event, [100, 100], dates=pd.DatetimeIndex([origin, origin + 2 * hour]))
            else:
                data = frame(event, [100])
                cache(strategy, data, entry_time)
                require(
                    exit_callback(strategy, trade, entry_time) is None, "Initial close rejected"
                )
            cache(strategy, data, now)
            require(
                exit_callback(strategy, trade, now) == "hs_policy_error_exit",
                f"{state} not rejected",
            )
            require(
                strategy.callback_errors[-1]["callback"] == "custom_exit", "Missing error receipt"
            )
            record(
                f"exit_{state}_history_policy_error",
                expected_policy_errors=len(strategy.callback_errors),
            )

        strategy, event, trade = fixture()
        cache(strategy, frame(event, [100]), entry_time)
        require(exit_callback(strategy, trade, entry_time) is None, "Initial close rejected")
        cache(strategy, frame(event, [100, 111, 100]), entry_time + 2 * hour)
        require(
            exit_callback(strategy, trade, entry_time + 2 * hour) == "hs_source_target",
            "Missed first crossing",
        )
        require(
            exit_callback(strategy, trade, entry_time + 2 * hour + pd.Timedelta(minutes=5))
            == "hs_source_target",
            "Terminal reason not persistent",
        )
        record("delayed_callback_scans_closed_rows_in_order_and_keeps_first_exit")

        require(
            exit_callback(strategy, trade, entry_time) == "hs_policy_error_exit",
            "Cached future exit leaked to an earlier clock",
        )
        require(
            strategy.callback_errors[-1]["callback"] == "custom_exit",
            "No backward-clock error receipt",
        )
        record("cached_exit_cannot_be_observed_before_its_source_close")

        for overlap in ("open", "closed_now", "bot_loop_block"):
            strategy, event, trade = fixture()
            cache(strategy, frame(event, [100]), entry_time)
            trade.enter_tag = "synthetic_old_opposite_event"
            trade.is_short = True
            trade.open_date = (entry_time - hour).to_pydatetime()
            if overlap == "closed_now":
                trade.is_open = False
                trade.close_date = entry_time.to_pydatetime()
                LocalTrade.bt_trades = [trade]
            else:
                LocalTrade.bt_trades_open = [trade]
                LocalTrade.bt_trades_open_pp[pair] = [trade]
                LocalTrade.bt_open_open_trade_count = 1
                if overlap == "bot_loop_block":
                    strategy.bot_loop_start(current_time=entry_time)
                    reset_globals()
            require(not confirm(strategy, event), f"{overlap} overlap accepted")
            require(not strategy.callback_errors, "Overlap fixture raised")
            record(f"entry_reject_{overlap}")

        for label, overrides in (
            ("unknown_tag", {"entry_tag": "unknown"}),
            ("wrong_side", {"side": "short"}),
            ("wrong_pair", {"pair": "NOT_IN_POOL/USDT:USDT"}),
            ("nonmarket", {"order_type": "limit"}),
        ):
            strategy, event, _ = fixture()
            cache(strategy, frame(event, [100]), entry_time)
            require(not confirm(strategy, event, **overrides), f"Invalid entry {label} accepted")
            require(not strategy.callback_errors, f"Invalid entry {label} raised")
            record(f"entry_reject_{label}")

        strategy, event, _ = fixture()
        cache(strategy, frame(event, [100], dates=pd.DatetimeIndex([origin - hour])), entry_time)
        require(not confirm(strategy, event), "Entry without confirmation row accepted")
        record("entry_missing_confirmation_rejected")

        strategy, event, _ = fixture()
        duplicate = frame(event, [100, 100], dates=pd.DatetimeIndex([origin, origin]))
        cache(strategy, duplicate, entry_time)
        require(
            not confirm(strategy, event),
            "Native safe-wrapper default True leaked through exception",
        )
        require(
            strategy.callback_errors[-1]["callback"] == "confirm_trade_entry",
            "No entry exception receipt",
        )
        record("entry_exception_safely_false_under_native_wrapper")

        strategy, event, trade = fixture()
        cache(strategy, frame(event, [100]), entry_time)
        trade.enter_tag = "unknown"
        require(
            exit_callback(strategy, trade, entry_time) == "hs_policy_error_exit",
            "Invalid trade binding not isolated",
        )
        require(
            strategy.callback_errors[-1]["callback"] == "custom_exit",
            "No exit binding error receipt",
        )
        record("exit_invalid_tag_has_independent_policy_error_reason")

        exits = strategy.should_exit(
            trade,
            100.0,
            entry_time.to_pydatetime(),
            enter=False,
            exit_=False,
            low=100.0,
            high=100.0,
        )
        require(
            len(exits) == 1
            and exits[0].exit_type == ExitType.CUSTOM_EXIT
            and exits[0].exit_reason == "hs_policy_error_exit",
            "Native should_exit mislabeled invalid policy as a source exit",
        )
        record("native_should_exit_policy_error_keeps_separate_reason")

        for kind, expected_type in (
            ("source", ExitType.CUSTOM_EXIT),
            ("catastrophe", ExitType.STOP_LOSS),
            ("liquidation", ExitType.LIQUIDATION),
        ):
            strategy, event, trade = fixture()
            now = entry_time + hour
            cache(strategy, frame(event, [100, 111 if kind == "source" else 100]), now)
            low = 100.0
            if kind == "catastrophe":
                low = 0.5  # Below the independent native -99% long stop at 1 USDT.
            elif kind == "liquidation":
                trade.set_liquidation_price(50.0)
                low = 49.0  # Liquidation reached while the -99% stop is not reached.
            exits = strategy.should_exit(
                trade,
                100.0,
                now.to_pydatetime(),
                enter=False,
                exit_=False,
                low=low,
                high=100.0,
            )
            require(
                [item.exit_type for item in exits] == [expected_type], f"Native exit type {kind}"
            )
            if kind == "source":
                require(exits[0].exit_reason == "hs_source_target", "Source exit reason changed")
            else:
                require(
                    not exits[0].exit_reason.startswith("hs_source_"), "Native risk exit mislabeled"
                )
            require(not strategy.callback_errors, f"Native should_exit fixture {kind} raised")
            record(f"native_should_exit_{kind}_distinct_type", native_exit_type=expected_type.value)

        require(all(not trade.orders for trade in detached), "Fixture created an order")
        receipt = {
            "passed": True,
            "fixture_kind": "SYNTHETIC_NATIVE_CALLBACK_INTERFACE_ONLY",
            "cases": results,
            "case_count": len(results),
            "native_provider_class": f"{DataProvider.__module__}.{DataProvider.__name__}",
            "strategy_class": f"{strategy_class.__module__}.{strategy_class.__name__}",
            "fresh_strategy_ft_bot_start_per_case": True,
            "caller_provider_mutated": False,
            "detached_trade_objects": len(detached),
            "synthetic_records_are_author_recognized_patterns": False,
            "market_source_recognition_run": False,
            "backtesting_start_called": False,
            "orders_created": 0,
            "fills_created": 0,
            "wallets_created": 0,
            "native_accounts_completed": 0,
            "financial_performance_proven": False,
        }
    finally:
        for name, value in original_globals.items():
            setattr(LocalTrade, name, value)
        Trade.use_db = original_use_db
        for strategy in strategies:
            strategy.ft_bot_cleanup()
    receipt["native_trade_globals_restored"] = (
        all(getattr(LocalTrade, name) is value for name, value in original_globals.items())
        and Trade.use_db == original_use_db
    )
    require(receipt["native_trade_globals_restored"], "Native global state not restored")
    return receipt
