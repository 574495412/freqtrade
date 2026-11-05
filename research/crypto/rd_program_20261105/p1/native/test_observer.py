"""Synthetic clock/state boundary checks, not financial backtests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from freqtrade.persistence import LocalTrade

import run_native
from observer import Observer


class ObserverBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old_trades, self.old_profit = LocalTrade.bt_trades_open, LocalTrade.bt_total_profit
        LocalTrade.bt_trades_open, LocalTrade.bt_total_profit = [], 0
        self.tmp = tempfile.TemporaryDirectory(prefix="observer_test_", dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.helper = self.root / "helper.py"
        self.helper.write_text("""
from freqtrade.persistence import LocalTrade
class Observer:
    maximum_identity_error = 0.0
    maximum_basis_rounding_bias = 0.0
    def __init__(self,account,plan,output): pass
    def trade_values(self,time,marks=False):
        return dict(equity=1000.0+LocalTrade.bt_total_profit,gross=100.0 if LocalTrade.bt_trades_open else 0.0,open_pnl=0.0)
""")
        self.plan = {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:30:00Z",
                     "comparison_end": "2026-01-01T00:00:00Z", "initial_equity_usdt": 1000,
                     "native_observer_source": {"path": str(self.helper)}}

    def tearDown(self):
        LocalTrade.bt_trades_open, LocalTrade.bt_total_profit = self.old_trades, self.old_profit
        self.tmp.cleanup()

    def account(self):
        class Account:
            config = {"trading_mode": "spot"}

            def time_pair_generator(self):
                for time in ("00:05", "00:20"):
                    yield (pd.Timestamp(f"2026-01-01T{time}:00Z"), "BTC/USDT", (), False, None)
        return Account()

    def test_flat_gaps_are_filled_and_yields_unchanged(self):
        account = self.account()
        original = list(account.time_pair_generator())
        observer = Observer(account, self.plan, self.root)
        self.assertEqual(list(account.time_pair_generator()), original)
        self.assertEqual(len(observer.five_minute_rows), 6)
        self.assertEqual(len(observer.rows), 1)
        self.assertEqual(observer.gaps, [])
        self.assertTrue(all(row["equity"] == 1000 for row in observer.five_minute_rows))

    def test_nonflat_gap_is_missing_not_interpolated(self):
        account = self.account()
        observer = Observer(account, self.plan, self.root)
        for index, _ in enumerate(account.time_pair_generator()):
            LocalTrade.bt_trades_open = [object()] if index == 0 else []
        dates = {row["date"].minute for row in observer.five_minute_rows}
        self.assertNotIn(10, dates)
        self.assertNotIn(15, dates)
        self.assertIn(25, dates)
        self.assertEqual(len(observer.gaps), 1)

    def test_pending_trade_object_also_blocks_flat_completion(self):
        account = self.account()
        observer = Observer(account, self.plan, self.root)
        for _ in account.time_pair_generator():
            LocalTrade.bt_trades_open = [type("Pending", (), {"has_open_position": False})()]
        self.assertGreater(len(observer.gaps), 0)
        self.assertNotEqual(len(observer.five_minute_rows), 6)

    def test_end_or_later_event_cannot_fill_prior_nonflat_gap(self):
        for boundary in ("00:30", "00:35"):
            with self.subTest(first_outside_window=boundary):
                LocalTrade.bt_trades_open, LocalTrade.bt_total_profit = [], 0

                class Account:
                    config = {"trading_mode": "spot"}

                    def time_pair_generator(self):
                        for time in ("00:05", boundary, "00:40"):
                            yield (pd.Timestamp(f"2026-01-01T{time}:00Z"), "BTC/USDT", (), False, None)

                account = Account()
                expected_yields = list(account.time_pair_generator())
                observer = Observer(account, self.plan, self.root)
                actual_yields = []
                for index, item in enumerate(account.time_pair_generator()):
                    actual_yields.append(item)
                    if index == 0:
                        LocalTrade.bt_trades_open = [object()]
                    else:
                        # A native exit outside the evaluation window must not
                        # retroactively supply cash for an earlier held position.
                        LocalTrade.bt_trades_open = []
                        LocalTrade.bt_total_profit = -100 * index
                self.assertEqual(actual_yields, expected_yields)
                self.assertEqual([row["date"].minute for row in observer.five_minute_rows], [0, 5])
                self.assertTrue(all(row["equity"] == 1000 for row in observer.five_minute_rows))
                self.assertEqual(len(observer.gaps), 1)
                self.assertFalse(observer.previous_flat)
                self.assertEqual(observer.previous_cash, 1000)
                receipt = json.loads((self.root / "five_minute_observer_receipt.json").read_text())
                self.assertEqual(receipt["missing_rows"], 4)
                self.assertFalse(receipt["complete_reference_calendar"])
                self.assertEqual(receipt["gap_records"][0]["to_exclusive"], "2026-01-01T00:30:00+00:00")

    def test_flat_window_tail_does_not_use_end_event_cash_change(self):
        class Account:
            config = {"trading_mode": "spot"}

            def time_pair_generator(self):
                for time in ("00:05", "00:30"):
                    yield (pd.Timestamp(f"2026-01-01T{time}:00Z"), "BTC/USDT", (), False, None)

        account = Account()
        observer = Observer(account, self.plan, self.root)
        for index, _ in enumerate(account.time_pair_generator()):
            if index == 1:
                LocalTrade.bt_total_profit = -100
        self.assertEqual(len(observer.five_minute_rows), 6)
        self.assertTrue(all(row["equity"] == 1000 for row in observer.five_minute_rows))
        self.assertEqual(observer.gaps, [])
        self.assertEqual(observer.previous_cash, 1000)


class NativeConfigurationTests(unittest.TestCase):
    def test_retained_hashes_exclude_cache_and_cover_durable_results(self):
        with tempfile.TemporaryDirectory(prefix="hash_test_", dir=Path(__file__).parent) as temporary:
            output = Path(temporary)
            (output / "native_results").mkdir()
            (output / "lossless_cache").mkdir()
            (output / "result.json").write_text('{"passed":true}')
            (output / "native_results" / "export.json").write_text('{}')
            (output / "lossless_cache" / "reconstructable.bin").write_bytes(b"not hashed")
            (output / "progress.json").write_text('{}')
            receipt = run_native.retain_result_hashes(output)
            artifact = run_native.guard.checked(receipt)
            records = json.loads(artifact.read_text())["files"]
            self.assertEqual([row["relative_path"] for row in records], ["native_results/export.json", "result.json"])
            self.assertTrue((output / "lossless_cache" / "reconstructable.bin").exists())
            self.assertEqual(records[1]["sha256"], run_native.guard.sha(output / "result.json"))

    def test_average_defaults_are_exact_and_no_nfi_override(self):
        for market in ("spot", "futures"):
            run = run_native.lookup(f"P1_B2_AVERAGE_NATIVE_{market}_S_base")
            plan, raw, _ = run_native.build_plan(run, Path(__file__).parent / "test_config_not_written.json")
            self.assertEqual((raw["timeframe"], raw["timeframe_detail"], raw["startup_candle_count"]), ("4h", "5m", 0))
            self.assertEqual(raw["minimal_roi"], {"0": 0.5})
            self.assertEqual(raw["stoploss"], -0.2)
            self.assertEqual((raw["stake_amount"], raw["max_open_trades"], raw["tradable_balance_ratio"]), ("unlimited", 6, 0.99))
            self.assertNotIn("nfi_parameters", raw)
            self.assertNotIn("grinding_enable", raw)
            self.assertFalse(raw["position_adjustment_enable"])
            self.assertFalse(plan["RD1_applied"])

    def test_non_registered_or_wrong_baseline_id_is_refused(self):
        for name in ("P1_B0_CASH_spot_S_base", "made_up_run"):
            with self.assertRaises(ValueError):
                run_native.lookup(name)

    def test_explicit_strategy_manifest_fields_preserve_b3_defaults(self):
        for market in ("spot", "futures"):
            for baseline, timeframe, detail, startup in (
                ("B2_AVERAGE_NATIVE", "4h", "5m", 0),
                ("B3_NFI_X8_NATIVE", "5m", None, 800),
            ):
                run = run_native.lookup(f"P1_{baseline}_{market}_S_base")
                plan, config, _ = run_native.build_plan(run, Path("test_config_not_written.json"))
                plan["candidates"][0]["config"]["sha256"] = "test-config-digest"
                fields = run_native.manifest_strategy_fields(plan, config)
                self.assertEqual(fields["resolved_strategy_config_hash"], "test-config-digest")
                self.assertEqual((fields["strategy_main_timeframe"], fields["detail_timeframe"], fields["startup_candle_count"]), (timeframe, detail, startup))
                self.assertEqual(fields["order_types"], config["order_types"])
                self.assertEqual(fields["stake_amount"], "unlimited")
                if baseline == "B3_NFI_X8_NATIVE":
                    self.assertNotIn("startup_candle_count", config)
                    self.assertNotIn("timeframe_detail", config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
