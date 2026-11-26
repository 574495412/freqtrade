"""Synthetic source metadata, real native DP/callback/timeout/shift interfaces.

No strategy-recognized market outcome, order execution, wallet or account is run.
"""

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from HeadShouldersLimit5mInputs import (
    FIVE,
    HOUR,
    NATIVE_START,
    OriginalInputs,
    OriginalStrategy,
    closed_hourly,
    merge_hourly,
)
from HeadShouldersLimit5mStrategy import HeadShouldersLimit5mStrategy
from freqtrade.configuration import TimeRange
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.optimize.backtesting import Backtesting, DATE_IDX, ENTER_TAG_IDX
from freqtrade.persistence import LocalTrade, Order, Trade


HERE = Path(__file__).resolve().parent
PAIR = "BTC/USDT:USDT"
ORIGIN = pd.Timestamp("2025-02-01T10:00:00Z")
RESULTS = []


def require(value, message):
    if not value:
        raise AssertionError(message)


def record(name, **details):
    RESULTS.append({"case": name, "passed": True, **details})


def config():
    value = json.loads(
        (HERE.parent / "head_shoulders_policy_native_20261117_v2/native_config.json").read_text()
    )
    value.update(
        timeframe="5m",
        timeframe_detail="",
        timerange="1735689600-1788220799",
        startup_candle_count=800,
        research_head_shoulders_limit5m=True,
        runmode=RunMode.BACKTEST,
        candle_type_def=CandleType.FUTURES,
        order_types=deepcopy(HeadShouldersLimit5mStrategy.order_types),
        order_time_in_force={"entry": "GTC", "exit": "GTC"},
    )
    return value


def fixture(inverted=True, width=4, origin=ORIGIN, self_terminal=False):
    LocalTrade.bt_trades = []
    LocalTrade.bt_trades_open = []
    LocalTrade.bt_trades_open_pp = defaultdict(list)
    LocalTrade.bt_open_open_trade_count = 0
    LocalTrade.bt_total_profit = 0.0
    strategy = HeadShouldersLimit5mStrategy(config())
    strategy.dp = DataProvider(strategy.config, exchange=None)
    strategy.ft_bot_start()
    require(hasattr(strategy, "_events"), "Native bot_start did not initialize policy")
    event = {
        "event_id": f"synthetic_{origin.value}_{inverted}",
        "pair": PAIR,
        "side": "long" if inverted else "short",
        "inverted": inverted,
        "break_i": 10,
        "head_width": width,
        "source_expiry_i": 10 + width - 1,
        "confirmation_bar_open": origin,
        "entry_available": origin + HOUR,
        "earliest_signal_available": origin + HOUR,
        "expiry_bar_open": origin + (width - 1) * HOUR,
        "expiry_available": origin + width * HOUR,
        "source_target_log_price": float(np.log(110 if inverted else 90)),
        "source_stop_log_price": float(np.log(90 if inverted else 110)),
        "self_terminal_at_confirmation": self_terminal,
    }
    strategy._events = {event["event_id"]: event}
    strategy._pair_events = {PAIR: [event]}
    trade = LocalTrade(
        id=1,
        pair=PAIR,
        enter_tag=event["event_id"],
        open_date=event["entry_available"].to_pydatetime(),
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
    return strategy, event, trade


def merged(event, closes, start=None):
    origin = event["confirmation_bar_open"]
    base_start = start or origin - 2 * HOUR
    hourly_start = min(origin - 3 * HOUR, base_start.floor("h") - HOUR)
    dates = pd.date_range(hourly_start, origin + (len(closes) + 1) * HOUR, freq="h")
    hourly = pd.DataFrame(
        {
            "date": dates,
            "close": 100.0,
            "hs_event_id": "",
            "hs_direction": 0,
            "hs_self_terminal": False,
        }
    )
    for number, close in enumerate(closes):
        hourly.loc[hourly.date.eq(origin + number * HOUR), "close"] = float(close)
    match = hourly.date.eq(origin)
    hourly.loc[match, "hs_event_id"] = event["event_id"]
    hourly.loc[match, "hs_direction"] = 1 if event["inverted"] else -1
    hourly.loc[match, "hs_self_terminal"] = event["self_terminal_at_confirmation"]
    base_dates = pd.date_range(base_start, dates[-1] + HOUR - FIVE, freq="5min")
    base = pd.DataFrame(
        {
            "date": base_dates,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )
    return merge_hourly(base, hourly)


def cache(strategy, data, now, expose_future=False):
    strategy.dp._set_cached_df(PAIR, "5m", data, CandleType.FUTURES)
    count = len(data) if expose_future else int(data.date.searchsorted(now))
    strategy.dp._set_dataframe_max_index(PAIR, count)
    strategy.dp._set_dataframe_max_date(now.to_pydatetime())


def confirm(strategy, event, now=None, **changes):
    args = dict(
        pair=PAIR,
        order_type="limit",
        amount=1.0,
        rate=100.0,
        time_in_force="GTC",
        current_time=now or event["entry_available"],
        entry_tag=event["event_id"],
        side=event["side"],
    )
    args.update(changes)
    return strategy.confirm_trade_entry(**args)


def exit_at(strategy, trade, now):
    return strategy.custom_exit(PAIR, trade, now, 100.0, 0.0)


def expect_error(callable_, name):
    try:
        callable_()
    except ValueError:
        record(name)
    else:
        raise AssertionError("Expected rejection: " + name)


def run():  # noqa: C901 - explicit fixture enumeration
    attributes = [
        "bt_trades",
        "bt_trades_open",
        "bt_trades_open_pp",
        "bt_open_open_trade_count",
        "bt_total_profit",
    ]
    previous = {name: getattr(LocalTrade, name) for name in attributes}
    previous_use_db = Trade.use_db
    try:
        Trade.use_db = False
        # Imported original functions and loaded author nodes stay exact.
        require(
            HeadShouldersLimit5mStrategy.populate_entry_trend
            is OriginalStrategy.populate_entry_trend,
            "Source signal direction algorithm was replaced",
        )
        require(
            HeadShouldersLimit5mStrategy.leverage is OriginalStrategy.leverage, "Leverage changed"
        )
        source, _ = OriginalInputs.original_source(config())
        _, nodes = source.load_author()
        require(len(nodes) == 8, "Original selected AST node count changed")
        record(
            "original_source_AST_and_inherited_geometry_identity",
            nodes=nodes,
            original_event_function_module=OriginalInputs.event_records.__module__,
        )

        strategy, event, trade = fixture(origin=NATIVE_START - HOUR)
        data = merged(event, [100] * 5)
        require(data.hs_event_id.eq(event["event_id"]).sum() == 0, "00:00 event was injected")
        strategy2, event2, _ = fixture(origin=NATIVE_START)
        data2 = merged(event2, [100] * 5, start=NATIVE_START - 800 * FIVE)
        carrying = data2.loc[data2.hs_event_id.eq(event2["event_id"]), "date"]
        require(list(carrying) == [NATIVE_START + HOUR - FIVE], "Event is not singular T-5m")
        # Actual owner conversion method, with only progress callbacks as no-ops.
        # No Backtesting instance, constructor, backtest, start, wallet or exchange.
        context = SimpleNamespace(
            strategy=strategy2,
            dataprovider=strategy2.dp,
            timeframe="5m",
            config=strategy2.config,
            required_startup=800,
            timerange=TimeRange.parse_timerange("1735689600-1788220799"),
            _set_progress_step=lambda *args: None,
            check_abort=lambda: None,
            _increment_progress=lambda: None,
        )
        rows = Backtesting._get_ohlcv_as_lists(context, {PAIR: data2.copy()})[PAIR]
        tagged = [row for row in rows if row[ENTER_TAG_IDX] == event2["event_id"]]
        require(
            len(tagged) == 1 and pd.Timestamp(tagged[0][DATE_IDX]) == NATIVE_START + HOUR,
            "Actual owner shift did not put entry at exact T",
        )
        require(pd.Timestamp(rows[0][DATE_IDX]) == NATIVE_START + FIVE, "First native row differs")
        record("native_trim_and_once_shift_00_event_lost_01_event_exact", actual_owner_method=True)

        # Information at T-5m is usable only when that base bar ends at T.
        strategy, event, trade = fixture()
        data = merged(event, [100, 111, 100, 100])
        now = event["entry_available"]
        cache(strategy, data, now - FIVE, expose_future=True)
        require(not confirm(strategy, event, now - FIVE), "Early clock accepted")
        require(not strategy._closed(PAIR, now - FIVE).date.eq(ORIGIN).any(), "Source hour early")
        cache(strategy, data, now, expose_future=True)
        require(confirm(strategy, event), "Exact LIMIT initial entry refused")
        require(not confirm(strategy, event), "Repeated event accepted")
        require(not confirm(strategy, event, now + FIVE), "Missed entry retried")
        record("closed_hour_visibility_exact_initial_repeat_and_late_rejection")

        for changes, name in [
            ({"order_type": "market"}, "market_rejected"),
            ({"time_in_force": "IOC"}, "nonGTC_rejected"),
            ({"entry_tag": "invalid"}, "invalid_tag_rejected"),
        ]:
            strategy, event, _ = fixture()
            cache(strategy, merged(event, [100] * 4), event["entry_available"])
            require(not confirm(strategy, event, **changes), name)
            record(name)
        strategy, event, trade = fixture(self_terminal=True)
        data = merged(event, [100] * 4)
        strategy.populate_entry_trend(data, {"pair": PAIR})
        require(not data.enter_long.any() and not data.enter_short.any(), "Self terminal signaled")
        cache(strategy, data, event["entry_available"])
        require(not confirm(strategy, event), "Self terminal accepted")
        record("source_self_terminal_mask")
        for closed in (False, True):
            strategy, event, trade = fixture()
            if closed:
                trade.is_open = False
                trade.close_date = event["entry_available"].to_pydatetime()
                LocalTrade.bt_trades = [trade]
            else:
                LocalTrade.bt_trades_open = [trade]
                LocalTrade.bt_trades_open_pp[PAIR] = [trade]
            cache(strategy, merged(event, [100] * 4), event["entry_available"])
            require(not confirm(strategy, event), "Same-pair overlap accepted")
            record("same_clock_closed_rejected" if closed else "existing_trade_rejected")

        for inverted in (True, False):
            for boundary in ("target", "stop"):
                level = (
                    (110 if inverted else 90) if boundary == "target" else (90 if inverted else 110)
                )
                crossing = level + (1 if (inverted == (boundary == "target")) else -1)
                strategy, event, trade = fixture(inverted)
                data = merged(event, [100, level, crossing, 100])
                for offset in (1, 2):
                    now = ORIGIN + offset * HOUR
                    cache(strategy, data, now)
                    require(
                        exit_at(strategy, trade, now) is None,
                        "Equality triggered strict source exit",
                    )
                now = ORIGIN + 3 * HOUR
                cache(strategy, data, now - FIVE, expose_future=True)
                require(exit_at(strategy, trade, now - FIVE) is None, "Unclosed crossing leaked")
                cache(strategy, data, now)
                require(
                    exit_at(strategy, trade, now) == f"hs_source_{boundary}",
                    "Strict crossing missed",
                )
                record(f"{'long' if inverted else 'short'}_strict_{boundary}_and_close_clock")
        strategy, event, trade = fixture()
        data = merged(event, [100] * 4)
        for offset in range(1, 5):
            now = ORIGIN + offset * HOUR
            cache(strategy, data, now)
            expected = "hs_source_expiry" if offset == 4 else None
            require(exit_at(strategy, trade, now) == expected, "Expiry changed")
        record("break_plus_width_minus_one_expiry")

        strategy, event, trade = fixture()
        data = merged(event, [100, 111, 100, 100])
        now = ORIGIN + 2 * HOUR
        cache(strategy, data, now, expose_future=True)
        baseline = strategy._closed(PAIR, now)
        shocked = data.copy()
        shocked.loc[shocked.date >= now, "close_hs1h"] *= 999
        cache(strategy, shocked, now, expose_future=True)
        pd.testing.assert_frame_equal(baseline, strategy._closed(PAIR, now), check_exact=True)
        record("future_base_rows_ignored_exact_prefix")
        for kind in ("conflict", "gap", "stale", "future"):
            bad = data.copy()
            if kind == "conflict":
                bad.loc[bad.date.eq(ORIGIN + HOUR), "close_hs1h"] = 101.0
            elif kind == "gap":
                bad = bad.loc[~bad.date.eq(ORIGIN + FIVE)].reset_index(drop=True)
            elif kind == "stale":
                bad.loc[bad.date.eq(ORIGIN + HOUR), "date_hs1h"] -= HOUR
            else:
                bad.loc[bad.date.eq(ORIGIN + HOUR), "date_hs1h"] += HOUR
            expect_error(lambda bad=bad: closed_hourly(bad, now), "reject_merged_" + kind)
        collision = data.drop(columns=[c for c in data if c.endswith("_hs1h")]).copy()
        collision["reserved_hs1h"] = 1
        expect_error(lambda: merge_hourly(collision, baseline), "reserved_suffix_collision")

        # Genuine native 1000-row slicing over more than 83 hours, with remembered progress.
        strategy, event, trade = fixture(width=102)
        data = merged(event, [100] * 101 + [111])
        max_slice = 0
        for offset in range(1, 103):
            now = ORIGIN + offset * HOUR
            cache(strategy, data, now)
            actual, _ = strategy.dp.get_analyzed_dataframe(PAIR, "5m")
            max_slice = max(max_slice, len(actual))
            require(len(actual) <= 1000, "Native DP slice cap bypassed")
            reason = exit_at(strategy, trade, now)
            require(reason == ("hs_source_target" if offset == 102 else None), "83h progress lost")
        require(
            max_slice == 1000 and not strategy.callback_errors,
            "Did not cross native slice boundary",
        )
        record("native_1000rows_83h20m_sequential_progress", callbacks=102, max_rows=max_slice)
        strategy, event, trade = fixture(width=102)
        data = merged(event, [100] * 102)
        now = ORIGIN + 101 * HOUR
        cache(strategy, data, now)
        require(
            exit_at(strategy, trade, now) == "hs_policy_error_exit", "83h missing history accepted"
        )
        require(strategy.callback_errors, "Missing history did not poison admission")
        record("skipped_83h_history_fails_closed")
        strategy, event, trade = fixture()
        data = merged(event, [100] * 4)
        now = ORIGIN + 2 * HOUR
        cache(strategy, data.loc[data.date < ORIGIN + HOUR].copy(), now)
        require(exit_at(strategy, trade, now) == "hs_policy_error_exit", "Stale source accepted")
        record("stale_hour_callback_independent_error")

        strategy, event, trade = fixture()
        data = merged(event, [100, 111, 100, 100])
        decision = ORIGIN + 2 * HOUR
        cache(strategy, data, decision)
        require(exit_at(strategy, trade, decision) == "hs_source_target", "Source decision absent")
        for delay in (0, 5, 10):
            now = decision + pd.Timedelta(minutes=delay)
            require(
                strategy.confirm_trade_exit(
                    PAIR, trade, "limit", 1, 100, "GTC", "hs_source_target", now
                ),
                "Later exit request rejected",
            )
        observations = [
            row for row in strategy.policy_audit if row["status"] == "SOURCE_DECISION_OBSERVED"
        ]
        requests = [
            row for row in strategy.policy_audit if row["status"] == "EXIT_REQUEST_CONFIRMED"
        ]
        require(len(observations) == 1, "First source decision rewritten")
        require(
            [row["request_delay_seconds"] for row in requests] == [0, 300, 600],
            "Request clocks collapsed",
        )
        require(all(row["fill_status"] == "UNKNOWN" for row in requests), "Request mislabeled fill")
        require(
            exit_at(strategy, trade, decision - FIVE) == "hs_policy_error_exit",
            "Backward clock accepted",
        )
        record(
            "first_source_decision_and_delayed_limit_requests_separate",
            observations=observations,
            requests=requests,
        )

        # Native timeout check on detached actual Orders; no fills or wallet.
        for side, expiry in ((trade.entry_side, 3), (trade.exit_side, 2)):
            order = Order(
                order_id=f"synthetic_timeout_{side}",
                ft_pair=PAIR,
                ft_order_side=side,
                ft_is_open=True,
                status="open",
                order_type="limit",
                side=side,
                price=100,
                amount=1,
                order_date=decision.to_pydatetime(),
            )
            require(
                not strategy.ft_check_timed_out(
                    trade, order, decision + pd.Timedelta(minutes=expiry) - pd.Timedelta(seconds=1)
                ),
                "Timeout early",
            )

            def forbidden_timeout(**kwargs):
                raise AssertionError("Native expired timeout called source callback")

            callback = "check_entry_timeout" if side == trade.entry_side else "check_exit_timeout"
            setattr(strategy, callback, forbidden_timeout)
            require(
                strategy.ft_check_timed_out(trade, order, decision + pd.Timedelta(minutes=expiry)),
                "Boundary timeout missed",
            )
            require(
                strategy.ft_check_timed_out(trade, order, decision + FIVE),
                "Next row timeout missed",
            )
            record(
                f"native_{'entry' if expiry == 3 else 'exit'}_timeout_{expiry}m_before_source_callback"
            )

        # No analyzed 1h cache was created. Empty/no-index handling cannot synthesize signal.
        strategy, event, trade = fixture()
        data = merged(event, [100] * 4)
        strategy.dp._set_cached_df(PAIR, "5m", data, CandleType.FUTURES)
        require(not confirm(strategy, event), "No-index DP cache accepted")
        keys = list(strategy.dp._DataProvider__cached_pairs)
        require(all(key[1] == "5m" for key in keys), "1h analyzed cache was used")
        record("native_noindex_empty_and_only5m_analyzed_cache")
        return {
            "passed": True,
            "case_count": len(RESULTS),
            "cases": RESULTS,
            "source_metadata": "synthetic, not market-recognized patterns",
            "actual_native_interfaces": [
                "DataProvider",
                "IStrategy.ft_bot_start",
                "ft_advise_signals",
                "Backtesting._get_ohlcv_as_lists unbound data conversion only",
                "IStrategy.ft_check_timed_out",
                "detached LocalTrade",
                "detached unfilled Order",
            ],
            "backtesting_constructed": False,
            "backtesting_start_called": False,
            "wallet_constructed": False,
            "orders_filled": 0,
            "financial_accounts": 0,
            "economic_result": "UNKNOWN",
            "full_lifecycle_admission": False,
            "future_required": [
                "actual entry cancellation before fill in separately admitted native run",
                "read-only raw Order creation/fill capture",
                "LIMIT fill/delay/cost reconciliation",
                "complete account native runner/resource lock/root release",
                "independent full account audit",
            ],
        }
    finally:
        for name, value in previous.items():
            setattr(LocalTrade, name, value)
        Trade.use_db = previous_use_db
