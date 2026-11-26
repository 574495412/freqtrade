"""Detached interface tests only: no Backtesting/Wallet/Exchange construction or start."""

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from . import execute_pair, native_producer
from .admission import HERE, checked, read
from .capture_native import CALLBACKS, Capture, filled_kind, typed


def rejected(operation, expected, contains):
    try:
        operation()
    except expected as error:
        if contains not in str(error):
            raise AssertionError(str(error)) from error
    else:
        raise AssertionError("Expected rejection: " + contains)


def run():
    observed = []
    with tempfile.TemporaryDirectory(prefix="nfi-lifecycle-objects-") as temporary:
        root = Path(temporary)
        # Gate order is tested with denied contexts only; never fabricate approval.
        rejected(lambda: native_producer._construct({}, root, object()), ValueError, "admitted")
        rejected(
            lambda: native_producer._run_admitted({}, "invalid", root, object()),
            ValueError,
            "admitted",
        )
        with (
            patch.object(
                native_producer,
                "shared_worker_admission",
                side_effect=ValueError("synthetic denied"),
            ),
            patch.object(
                native_producer, "_construct", side_effect=AssertionError("constructor reached")
            ),
        ):
            rejected(
                lambda: native_producer.run_variant(native_producer.VARIANTS[0]),
                ValueError,
                "denied",
            )
        with (
            patch.object(
                execute_pair, "require_release", side_effect=ValueError("synthetic denied")
            ),
            patch.object(
                execute_pair.subprocess, "Popen", side_effect=AssertionError("process reached")
            ),
        ):
            rejected(execute_pair.run, ValueError, "denied")
        if "freqtrade.optimize.backtesting" in sys.modules:
            raise AssertionError("Gates imported native Backtesting")
        observed.append("constructor_and_controller_denial_precede_native_import_or_process")

        plan = read(HERE / "plan.json")
        for path, digest in plan["native_owners"].items():
            checked({"path": path, "sha256": digest})
        sys.path.insert(0, plan["engine_source"])
        from freqtrade.data.dataprovider import DataProvider
        from freqtrade.enums import CandleType, RunMode
        from freqtrade.persistence import LocalTrade, Order
        from research.crypto.nfi40_frame_cache_native_20261117_v1.frame_storage_v3 import (
            install,
            semantic_digest,
        )

        legacy = native_producer.load(
            "object_fixture_original_zstd", plan["variants"][0]["storage_source"]
        )

        # Actual official provider, no exchange or wallet; all frames generated here.
        config = {"runmode": RunMode.BACKTEST, "candle_type_def": CandleType.FUTURES}
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2025-03-01", periods=30, freq="5min", tz="UTC"),
                "close": [100 + i / 100 for i in range(30)],
                "enter_tag": [None, "synthetic", ""] * 10,
            }
        )
        frame.attrs["synthetic"] = True
        provider_summaries = []
        for mode in native_producer.VARIANTS:
            output = root / mode
            output.mkdir()
            provider = DataProvider(config, None)
            stores = []
            if mode.endswith("lru32"):
                stores = install(provider, output / "blocks")
            else:
                native_clear = provider.clear_cache
                generations = []

                def clear_legacy():
                    native_clear()
                    mapping = legacy.DiskMapping(output / "legacy" / str(len(generations)), 4)
                    provider._DataProvider__cached_pairs = mapping
                    generations.append(mapping)

                provider.clear_cache = clear_legacy
                clear_legacy()
            trace = Capture(
                output, lambda: {"synthetic": True, "simulation_clock": "2025-03-01T00:00:00Z"}
            )
            trace.install_provider(provider, mode)
            trace.phase = "native_reset"
            provider.clear_cache()
            provider._set_cached_df("BTC/USDT:USDT", "5m", frame, CandleType.FUTURES)
            empty, _ = provider.get_analyzed_dataframe("BTC/USDT:USDT", "5m")
            assert empty.empty
            provider._set_dataframe_max_index("BTC/USDT:USDT", 20)
            sliced, _ = provider.get_analyzed_dataframe("BTC/USDT:USDT", "5m")
            pd.testing.assert_frame_equal(
                sliced,
                frame.iloc[:20],
                check_exact=True,
                check_dtype=True,
                check_index_type=True,
                check_column_type=True,
            )
            assert semantic_digest(sliced) == semantic_digest(frame.iloc[:20])

            class SyntheticCallbacks:
                def ft_advise_signals(self, value, metadata):
                    return value

            strategy = SyntheticCallbacks()
            calls = []

            def harmless(*args, **kwargs):
                return None

            for callback in CALLBACKS:
                setattr(strategy, callback, harmless)

            def custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs):
                calls.append(current_time)
                provider.get_analyzed_dataframe(pair, "5m")
                return "synthetic_reason"

            strategy.custom_exit = custom_exit
            trace.install_strategy(strategy)
            assert strategy.ft_advise_signals(frame, {"pair": "BTC/USDT:USDT"}) is frame
            clock = datetime(2025, 3, 1, 1, 40, tzinfo=UTC)
            assert (
                strategy.custom_exit("BTC/USDT:USDT", None, clock, 100.0, -0.001)
                == "synthetic_reason"
            )
            assert calls == [clock]
            trace.phase = "post_cleanup_verification"
            provider.clear_cache()
            for pair in ("BTC/USDT:USDT", "MISSING/USDT:USDT"):
                empty, refreshed = provider.get_analyzed_dataframe(pair, "5m")
                assert empty.empty and refreshed == datetime.fromtimestamp(0, UTC)
            trace.fail_if_errors()
            trace.close()
            for store in stores:
                store.flush()
            provider_summaries.append(trace.records)
        assert provider_summaries[0] == provider_summaries[1]
        observed.append(
            "actual_official_DP_object_slice_noindex_missing_clear_callback_semantics_equal"
        )

        boundary_output = root / "synthetic_boundary_counter"
        boundary_output.mkdir()
        provider = DataProvider(config, None)
        install(provider, boundary_output / "blocks")
        trace = Capture(boundary_output, lambda: {"synthetic": True})
        trace.install_provider(provider, native_producer.VARIANTS[1])
        boundary_frame = pd.DataFrame(
            {
                "date": pd.date_range("2025-03-01", periods=5000, freq="5min", tz="UTC"),
                "close": [100.0] * 5000,
            }
        )
        provider._set_cached_df("BTC/USDT:USDT", "5m", boundary_frame, CandleType.FUTURES)
        provider._set_dataframe_max_index("BTC/USDT:USDT", 4500)
        strategy = SyntheticCallbacks()
        for callback in CALLBACKS:
            setattr(strategy, callback, harmless)

        def boundary_callback(pair, current_time, **kwargs):
            value, _ = provider.get_analyzed_dataframe(pair, "5m")
            pd.testing.assert_frame_equal(value, boundary_frame.iloc[3500:4500], check_exact=True)
            return "synthetic_boundary"

        strategy.custom_exit = boundary_callback
        trace.install_strategy(strategy)
        trace.phase = "native_matching"  # Synthetic phase marker, never lifecycle evidence.
        assert strategy.custom_exit("BTC/USDT:USDT", clock) == "synthetic_boundary"
        assert trace.cache_path_evidence == {
            "native_callback_dp_reads": 1,
            "native_callback_cross_block_reads": 1,
        }
        trace.fail_if_errors()
        trace.close()
        observed.append("synthetic_official_DP_4096_boundary_read_counter_records_actual_slice")

        output = root / "mutation"
        output.mkdir()
        provider = DataProvider(config, None)
        trace = Capture(output, lambda: {})
        trace.install_provider(provider, native_producer.VARIANTS[0])
        provider._set_cached_df("BTC/USDT:USDT", "5m", frame.copy(deep=True), CandleType.FUTURES)
        provider._set_dataframe_max_index("BTC/USDT:USDT", 20)
        strategy = SyntheticCallbacks()
        for callback in CALLBACKS:
            setattr(strategy, callback, harmless)

        def mutation(pair, current_time, **kwargs):
            value, _ = provider.get_analyzed_dataframe(pair, "5m")
            value.iloc[0, 1] = 999.0

        strategy.custom_exit = mutation
        trace.install_strategy(strategy)
        rejected(lambda: strategy.custom_exit("BTC/USDT:USDT", clock), ValueError, "mutated")
        rejected(trace.fail_if_errors, RuntimeError, "callback/storage")
        assert trace.errors[0]["where"] == "custom_exit"
        trace.close()
        observed.append("source_borrowed_frame_mutation_is_recorded_and_rejected")

        # Detached native data objects only: no matching, close_bt_order, global
        # trade registration, wallet, generated native fill or account lifecycle.
        trade = LocalTrade(
            pair="BTC/USDT:USDT",
            open_date=clock.replace(tzinfo=None),
            amount=1.000000000123,
            stake_amount=100.000000000123,
            open_rate=100.0,
            fee_open=0.0015,
            fee_close=0.0015,
        )
        order = Order(
            ft_order_side="buy",
            ft_pair=trade.pair,
            ft_is_open=True,
            ft_amount=1.000000000123,
            ft_price=100.000000000123,
            order_id="synthetic",
            order_type="limit",
            side="buy",
            status="open",
            amount=1.000000000123,
            price=100.000000000123,
            average=100.000000000123,
            filled=0.0,
            remaining=1.000000000123,
            order_date=clock.replace(tzinfo=None),
        )
        order._trade_bt = trade
        trade.orders.append(order)
        encoded = typed(trade)
        assert encoded["raw_trade_fields"]["amount"] == {"float": trade.amount.hex()}
        assert encoded["all_orders"][0]["raw_order_fields"]["average"] == {
            "float": order.average.hex()
        }
        assert encoded["native_trade"]["amount"] != encoded["raw_trade_fields"]["amount"]
        json.dumps(encoded, allow_nan=False)
        observed.append("detached_native_raw_fields_preserve_sub_8_decimal_values")
        rejected(lambda: typed(object()), TypeError, "Unsupported")

        synthetic_trade = SimpleNamespace(entry_side="buy", nr_of_successful_entries=1, amount=1.0)
        synthetic_order = SimpleNamespace(
            ft_is_open=False,
            status="closed",
            filled=1.0,
            ft_order_side="buy",
            safe_amount_after_fee=1.0,
        )
        assert filled_kind(synthetic_trade, synthetic_order) == "entry"
        synthetic_trade.nr_of_successful_entries = 2
        assert filled_kind(synthetic_trade, synthetic_order) == "position_add"
        synthetic_order.ft_order_side = "sell"
        synthetic_order.safe_amount_after_fee = 0.5
        assert filled_kind(synthetic_trade, synthetic_order) == "partial_exit"
        synthetic_order.safe_amount_after_fee = 1.0
        assert filled_kind(synthetic_trade, synthetic_order) == "full_exit"
        synthetic_order.ft_is_open = True
        rejected(lambda: filled_kind(synthetic_trade, synthetic_order), ValueError, "filled native")
        observed.append("synthetic_order_callback_stage_classification_and_unfilled_rejection")
        if "freqtrade.optimize.backtesting" in sys.modules:
            raise AssertionError("Object tests imported Backtesting")

    return {
        "passed": True,
        "checks": observed,
        "actual_native_accounts_started": 0,
        "actual_lifecycle_coverage": "UNKNOWN",
        "synthetic_fixtures_only": True,
        "actual_native_DP_object_used": True,
        "native_Backtesting_imported": False,
        "shared_financial_lock_acquired": False,
        "release_created": False,
        "execution_replacement_admitted": False,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
