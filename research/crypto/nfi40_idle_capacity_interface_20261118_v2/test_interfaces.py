"""Bounded real strategy resolver and pure-object fixtures; no financial engine."""

import copy
import hashlib
import json
import resource
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


HERE = Path(__file__).absolute().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guard(*args, **kwargs):
    raise AssertionError("No Backtesting, Wallet or Exchange construction or financial start")


def rejected(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Forbidden enabled/production/capital mode was accepted")


def main():  # noqa: C901 - bounded owner/interface fixtures
    freeze = json.loads((HERE / "freeze.json").read_text())
    for path, digest in freeze["files"].items():
        assert sha(path) == digest
    protocol = json.loads((HERE / "protocol.json").read_text())
    vendor = Path(protocol["vendor_root"])
    manifest = json.loads(Path(protocol["vendor_manifest"]["path"]).read_text())
    assert len(manifest["files"]) == 595
    for relative, digest in manifest["files"].items():
        assert sha(vendor / relative) == digest
    sys.path.insert(0, str(vendor))
    sys.path.insert(0, str(HERE))
    from CompletedIntentCalendar import CompletedIntentCalendar, direction
    from NFIIdleCapacityDisabled import NFIIdleCapacityDisabled, PinnedNFI

    from freqtrade.configuration import TimeRange
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.exchange.exchange import Exchange
    from freqtrade.optimize.backtesting import HEADERS, Backtesting
    from freqtrade.persistence import LocalTrade, Trade
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.strategy import IStrategy
    from freqtrade.wallets import Wallets

    results = []
    callbacks = protocol["unchanged_callbacks"]
    for name in callbacks:
        assert getattr(NFIIdleCapacityDisabled, name) is getattr(PinnedNFI, name), name
    results.append({"case": "all_callback_function_objects_inherited", "callbacks": len(callbacks)})
    assert issubclass(NFIIdleCapacityDisabled, IStrategy)
    globals_names = (
        "bt_trades",
        "bt_trades_open",
        "bt_trades_open_pp",
        "bt_open_open_trade_count",
        "bt_total_profit",
    )
    saved = {name: getattr(LocalTrade, name) for name in globals_names}
    saved_db = Trade.use_db
    try:
        Trade.use_db = False
        LocalTrade.bt_trades = []
        LocalTrade.bt_trades_open = []
        LocalTrade.bt_trades_open_pp = defaultdict(list)
        LocalTrade.bt_open_open_trade_count = 0
        LocalTrade.bt_total_profit = 0.0
        with (
            TemporaryDirectory(dir=HERE, prefix="synthetic_cache_") as temporary,
            patch.object(Backtesting, "__init__", guard),
            patch.object(Backtesting, "start", guard),
            patch.object(Wallets, "__init__", guard),
            patch.object(Exchange, "__init__", guard),
            patch("socket.socket.connect", guard),
            patch("socket.create_connection", guard),
        ):
            config = json.loads(Path(protocol["reference_config"]["path"]).read_text())
            config.update(
                runmode=RunMode.BACKTEST,
                user_data_dir=Path(temporary),
                dry_run=True,
                dry_run_wallet=1000,
                research_idle_aux_enabled=False,
            )
            # Real official strategy resolver for the local wrapper, no Backtesting instance.
            resolved_config = copy.deepcopy(config)
            resolved_config.update(strategy="NFIIdleCapacityDisabled", strategy_path=str(HERE))
            wrapped = StrategyResolver.load_strategy(resolved_config)
            baseline = PinnedNFI(copy.deepcopy(config))
            assert wrapped.timeframe == baseline.timeframe == "5m"
            assert wrapped.startup_candle_count == baseline.startup_candle_count == 800
            assert wrapped.order_types["entry"] == wrapped.order_types["exit"] == "limit"
            assert wrapped.position_adjustment_enable and wrapped.stoploss == -0.99
            assert wrapped.version() == baseline.version() == "v18.0.146"
            results.append(
                {
                    "case": "real_strategy_resolver_and_original_constructor",
                    "IStrategy": True,
                    "timeframe": "5m",
                    "entry_exit": "limit",
                    "startup": 800,
                    "strategy_name_different": wrapped.get_strategy_name()
                    != baseline.get_strategy_name(),
                }
            )
            for change in (
                {"research_idle_aux_enabled": True},
                {"research_idle_aux_enabled": None},
                {"runmode": RunMode.DRY_RUN},
                {"available_capital": 1000},
            ):
                invalid = copy.deepcopy(config)
                invalid.update(change)
                rejected(lambda invalid=invalid: NFIIdleCapacityDisabled(invalid))
            results.append({"case": "four_unadmitted_modes_rejected"})
            now = datetime(2025, 1, 1, 1, tzinfo=UTC)
            dummy = pd.DataFrame({"close": [100.0]})
            provider = SimpleNamespace(
                runmode=RunMode.BACKTEST,
                get_analyzed_dataframe=lambda pair, timeframe: (dummy, now),
                orderbook=lambda pair, n: {"bids": [[100.0, 1.0]], "asks": [[100.0, 1.0]]},
            )
            baseline.dp = wrapped.dp = provider
            for tag, side in [("1 2 ", "long"), ("61 ", "long"), ("501 502 ", "short")]:
                kwargs = {
                    "pair": "BTC/USDT:USDT",
                    "order_type": "limit",
                    "amount": 1.0,
                    "rate": 100.0,
                    "time_in_force": "GTC",
                    "current_time": now,
                    "entry_tag": tag,
                    "side": side,
                }
                assert baseline.confirm_trade_entry(**kwargs) == wrapped.confirm_trade_entry(
                    **kwargs
                )
                leverage = {
                    "pair": kwargs["pair"],
                    "current_time": now,
                    "current_rate": 100.0,
                    "proposed_leverage": 1.0,
                    "max_leverage": 20.0,
                    "entry_tag": tag,
                    "side": side,
                }
                assert baseline.leverage(**leverage) == wrapped.leverage(**leverage) == 1.0
                stake = {
                    "pair": kwargs["pair"],
                    "current_time": now,
                    "current_rate": 100.0,
                    "proposed_stake": 165.0,
                    "min_stake": 5.0,
                    "max_stake": 990.0,
                    "leverage": 1.0,
                    "entry_tag": tag,
                    "side": side,
                }
                assert baseline.custom_stake_amount(**stake) == wrapped.custom_stake_amount(**stake)
                assert kwargs["entry_tag"] == tag
            results.append(
                {
                    "case": "original_numeric_whitespace_tags_and_entry_stake_leverage",
                    "tags": ["1 2 ", "61 ", "501 502 "],
                }
            )
            frame = pd.DataFrame(
                {
                    "date": pd.date_range(now, periods=3, freq="5min"),
                    "close": [100.0, 99.0, 101.0],
                    "enter_tag": ["1 2 ", "61 ", "501 502 "],
                }
            )
            left = baseline.populate_exit_trend(frame.copy(), {"pair": "BTC/USDT:USDT"})
            right = wrapped.populate_exit_trend(frame.copy(), {"pair": "BTC/USDT:USDT"})
            pd.testing.assert_frame_equal(left, right)
            assert right.enter_tag.tolist() == frame.enter_tag.tolist()
            results.append({"case": "original_exit_vector_preserves_tag_bytes"})
            for first_entry, order_tag, exit_amount in [
                (True, "1 2 ", 2.0),
                (False, "derisk_level_1", 1.0),
                (False, "grind_1_exit", 1.0),
            ]:
                state = []
                for strategy in (baseline, wrapped):
                    custom = {}
                    trade = SimpleNamespace(
                        is_open=True,
                        is_short=False,
                        enter_tag="1 2 ",
                        entry_side="buy",
                        exit_side="sell",
                        amount=2.0,
                        nr_of_successful_entries=1,
                        nr_of_successful_exits=0 if first_entry else 1,
                        select_filled_orders=lambda side: [],
                        set_custom_data=lambda key, value, custom=custom: custom.__setitem__(
                            key, value
                        ),
                    )
                    order = SimpleNamespace(
                        ft_order_side="buy" if first_entry else "sell",
                        safe_amount_after_fee=exit_amount,
                        ft_order_tag=order_tag,
                    )
                    strategy.order_filled("BTC/USDT:USDT", trade, order, now)
                    state.append(custom)
                    assert trade.enter_tag == "1 2 " and order.ft_order_tag == order_tag
                assert state[0] == state[1] and state[0]
            results.append(
                {
                    "case": "real_NFI_order_filled_custom_data_dispatch",
                    "argument_fixture_cases": 3,
                    "native_order_execution": False,
                }
            )
            for short in (False, True):
                trade = SimpleNamespace(is_short=short)
                order = SimpleNamespace(price=100.0, ft_order_tag=None)
                assert baseline.check_entry_timeout(
                    "BTC/USDT:USDT", trade, order, now
                ) == wrapped.check_entry_timeout("BTC/USDT:USDT", trade, order, now)
                assert baseline.check_exit_timeout(
                    "BTC/USDT:USDT", trade, order, now
                ) == wrapped.check_exit_timeout("BTC/USDT:USDT", trade, order, now)
            baseline.bot_loop_start(now)
            wrapped.bot_loop_start(now)
            results.append({"case": "original_timeout_and_backtest_loop_delegation"})

            # Native signal conversion fixture: hand-labelled synthetic signals, not NFI output.
            class SyntheticSignals(IStrategy):
                timeframe = "5m"

                def populate_indicators(self, dataframe, metadata):
                    return dataframe

                def populate_entry_trend(self, dataframe, metadata):
                    dataframe["enter_long"] = dataframe.fixture_signal
                    dataframe["enter_short"] = 0
                    dataframe["enter_tag"] = dataframe.fixture_tag
                    return dataframe

                def populate_exit_trend(self, dataframe, metadata):
                    dataframe["exit_long"] = 0
                    dataframe["exit_short"] = 0
                    return dataframe

            signal_config = {
                "runmode": RunMode.BACKTEST,
                "timeframe": "5m",
                "candle_type_def": CandleType.FUTURES,
            }
            signal_strategy = SyntheticSignals(signal_config)
            dp = DataProvider(signal_config, exchange=None)
            times = pd.date_range("2024-12-31T23:55Z", "2025-01-01T01:05Z", freq="5min")
            data = pd.DataFrame(
                {
                    "date": times,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1.0,
                    "fixture_signal": 0,
                    "fixture_tag": "",
                }
            )
            data.loc[
                data.date.eq(pd.Timestamp("2024-12-31T23:55Z")), ["fixture_signal", "fixture_tag"]
            ] = [1, "hs_first_boundary"]
            data.loc[
                data.date.eq(pd.Timestamp("2025-01-01T00:55Z")), ["fixture_signal", "fixture_tag"]
            ] = [1, "hs_later_boundary"]
            owner = SimpleNamespace(
                strategy=signal_strategy,
                dataprovider=dp,
                timeframe="5m",
                config=signal_config,
                required_startup=0,
                timerange=TimeRange.parse_timerange("20250101-20250102"),
                _set_progress_step=lambda *args: None,
                check_abort=lambda: None,
                _increment_progress=lambda: None,
            )
            rows = Backtesting._get_ohlcv_as_lists(owner, {"BTC/USDT:USDT": data.copy()})[
                "BTC/USDT:USDT"
            ]
            date_i, tag_i = HEADERS.index("date"), HEADERS.index("enter_tag")
            assert pd.Timestamp(rows[0][date_i]) == pd.Timestamp("2025-01-01T00:05Z")
            assert not any(row[tag_i] == "hs_first_boundary" for row in rows)
            later = [row for row in rows if row[tag_i] == "hs_later_boundary"]
            assert len(later) == 1 and pd.Timestamp(later[0][date_i]) == pd.Timestamp(
                "2025-01-01T01:00Z"
            )
            results.append(
                {
                    "case": "actual_native_shift_and_start_boundary",
                    "first_executable_clock": str(rows[0][date_i]),
                    "00:00_event_inherited_unexecutable_boundary": True,
                    "01:00_projection_correct": True,
                }
            )
            # Real DP slices reproduce the stale global view before current-pair updates.
            for pair in ("A/USDT:USDT", "B/USDT:USDT"):
                dp._set_cached_df(pair, "5m", data, CandleType.FUTURES)
                dp._set_dataframe_max_index(pair, 2)
            before, _ = dp.get_analyzed_dataframe("B/USDT:USDT", "5m")
            dp._set_dataframe_max_index("A/USDT:USDT", 3)
            advanced, _ = dp.get_analyzed_dataframe("A/USDT:USDT", "5m")
            still_before, _ = dp.get_analyzed_dataframe("B/USDT:USDT", "5m")
            assert before.date.iloc[-1] == still_before.date.iloc[-1] < advanced.date.iloc[-1]
            results.append({"case": "real_DP_per_pair_slice_is_not_global_current_intent_view"})
    finally:
        Trade.use_db = saved_db
        for name, value in saved.items():
            setattr(LocalTrade, name, value)
    # Independent read-only calendar component; no enabled strategy injection.
    pairs = protocol["ordered_pairs"]
    calendar = CompletedIntentCalendar(pairs)
    future_calendar = CompletedIntentCalendar(pairs)
    current = pd.Timestamp("2025-01-01T01:00Z")
    source = pd.DataFrame(
        {
            "date": pd.date_range(current - pd.Timedelta(minutes=5), periods=3, freq="5min"),
            "enter_long": [0, 0, 0],
            "enter_short": [0, 0, 0],
            "exit_long": [0, 0, 0],
            "exit_short": [0, 0, 0],
            "enter_tag": ["", "", ""],
        }
    )
    for pair in pairs:
        original = source.copy()
        if pair == pairs[-1]:
            original.loc[0, ["enter_long", "enter_tag"]] = [1, "1 2 "]
        changed = original.copy()
        changed.loc[1:, ["enter_long", "enter_tag"]] = [1, "61 "]
        calendar.capture(pair, original)
        future_calendar.capture(pair, changed)
    snapshot = calendar.snapshot(current)
    assert snapshot == future_calendar.snapshot(current)
    assert (
        len(snapshot) == 1
        and snapshot[0]["pair"] == pairs[-1]
        and snapshot[0]["enter_tag"] == "1 2 "
    )
    results.append(
        {
            "case": "full40_completed_intent_calendar_future_shock",
            "pairs": len(pairs),
            "future_rows_read_by_snapshot": False,
        }
    )
    rejected(lambda: CompletedIntentCalendar(pairs).snapshot(current))
    rejected(lambda: calendar.snapshot(current + pd.Timedelta(hours=2)))
    results.append({"case": "missing_pair_or_exact_clock_never_uses_stale_fallback"})
    for flags in product((0, 1), repeat=4):
        le, se, lx, sx = flags
        row = [None] * len(HEADERS)
        for key, value in zip(
            ("enter_long", "enter_short", "exit_long", "exit_short"), flags, strict=True
        ):
            row[HEADERS.index(key)] = value
        native = Backtesting.check_for_trade_entry(SimpleNamespace(_can_short=True), tuple(row))
        assert direction(le, se, lx, sx) == native
    results.append(
        {"case": "direction_conflicts_match_actual_native_method", "flag_combinations": 16}
    )
    output = {
        "passed": True,
        "cases": results,
        "case_count": len(results),
        "official_files_verified": 595,
        "NFI_version": "v18.0.146",
        "callback_identity_count": len(callbacks),
        "full_native_account_parity": "UNKNOWN",
        "aux_enabled_implementation": "NOT_IMPLEMENTED_HARD_REJECTED",
        "Backtesting_instances": 0,
        "Backtesting_start_calls": 0,
        "Wallet_instances": 0,
        "financial_accounts_run": 0,
        "native_matching_proven": False,
        "peak_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "freeze_sha256": sha(HERE / "freeze.json"),
    }
    (HERE / "validation.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        (HERE / "failure.json").write_text(
            json.dumps(
                {"type": type(error).__name__, "message": str(error), "code_sha256": sha(__file__)},
                indent=2,
            )
            + "\n"
        )
        raise
