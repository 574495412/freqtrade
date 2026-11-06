"""Synthetic timing/contract checks; not financial backtests or live integration."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE / "sources"))
from factor_inputs import daily_features, cross_section, effective_daily_scale, scale_risk_approved_stake
from freqtrade.configuration import TimeRange
from freqtrade.enums import CandleType, RunMode
from freqtrade.exceptions import OperationalException
from freqtrade.optimize.backtesting import Backtesting, HEADERS
from freqtrade.resolvers import StrategyResolver

spec = importlib.util.spec_from_file_location("receipt_strict_features", ROOT / "research/crypto/rd_program_20261105/factors/features.py")
strict = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = strict
spec.loader.exec_module(strict)


def candles(freq="1h", periods=300, growth=.001):
    dates = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    close = np.exp(np.arange(periods) * growth + .005 * np.sin(np.arange(periods))) * 100
    return pd.DataFrame({"date": dates, "open": close, "high": close*1.01,
                         "low": close*.99, "close": close, "volume": 100.0})


def resolved(name, universe=("BTC/USDT",)):
    cfg = json.loads((ROOT / "research/crypto/official_factor_20261106/configs/spot_base.json").read_text())
    cfg.update(strategy=name, strategy_path=str(HERE / "sources"),
               user_data_dir=ROOT / "user_data/crypto_quant/official_factor_20261106/native_userdir",
               runmode=RunMode.BACKTEST)
    cfg["exchange"]["pair_whitelist"] = list(universe)
    return StrategyResolver.load_strategy(cfg)


class NativeFactors(unittest.TestCase):
    def test_timezone_representation_does_not_move_the_decision(self):
        h = candles()
        utc_day = pd.DatetimeIndex([pd.Timestamp("2026-01-10", tz="UTC")])
        utc_result = daily_features(h, utc_day)
        h["date"] = h.date.dt.tz_convert("Asia/Shanghai")
        pd.testing.assert_frame_equal(utc_result, daily_features(h, utc_day.tz_convert("Asia/Shanghai")))

    def test_formula_agrees_with_receipt_strict_reference_on_synthetic_known_receipts(self):
        h = candles()
        at = pd.Timestamp("2026-01-10", tz="UTC")
        rows = [strict.HourClose(r.date.to_pydatetime(), r.close, (r.date + pd.Timedelta(hours=1)).to_pydatetime()) for r in h.itertuples()]
        values = daily_features(h, pd.DatetimeIndex([at])).iloc[0]
        self.assertAlmostEqual(values.momentum168, strict.momentum168(rows, at.to_pydatetime()).value, places=12)
        self.assertAlmostEqual(values.scale24, strict.volatility24_scale(rows, at.to_pydatetime()).value, places=12)

    def test_future_perturbations_leave_past_cross_section_unchanged(self):
        days = pd.date_range("2026-01-09", "2026-01-10", tz="UTC")
        frames = {str(i): candles(growth=.001*(i+1)) for i in range(4)}
        before = cross_section(frames, tuple(frames), days)
        changed = copy.deepcopy(frames)
        for h in changed.values():
            h.loc[h.date >= days[-1], "close"] *= 1000
        after = cross_section(changed, tuple(frames), days)
        for a, b in zip(before, after):
            if isinstance(a, pd.DataFrame): pd.testing.assert_frame_equal(a, b)
            else: pd.testing.assert_series_equal(a, b)

    def test_missing_member_blocks_universe_and_ties_use_frozen_order(self):
        day = pd.DatetimeIndex([pd.Timestamp("2026-01-10", tz="UTC")])
        universe = ("D", "C", "B", "A")
        frames = {p: candles() for p in reversed(universe)}
        _, chosen, valid = cross_section(frames, universe, day)
        self.assertTrue(valid.iloc[0])
        self.assertEqual(list(chosen.columns[chosen.iloc[0]]), ["D", "C", "B"])
        frames["A"] = frames["A"].drop(index=100)
        _, chosen, valid = cross_section(frames, universe, day)
        self.assertFalse(valid.iloc[0])
        self.assertFalse(chosen.any().any())

    def test_zero_volatility_and_missing_hour_do_not_become_zero_signal_values(self):
        h = candles(); day = pd.DatetimeIndex([pd.Timestamp("2026-01-10", tz="UTC")])
        h["close"] = 100.0
        self.assertTrue(pd.isna(daily_features(h, day).iloc[0].scale24))
        h = h.drop(index=214)
        self.assertTrue(daily_features(h, day).iloc[0].isna().all())

    def test_native_signal_conversion_enters_at_next_five_minutes(self):
        strategy = resolved("F1Momentum168Native")
        h = candles()
        strategy.dp = SimpleNamespace(get_pair_dataframe=lambda **kw: h)
        main = candles("5min", 12*24*10)
        indicators = strategy.populate_indicators(main, {"pair": "BTC/USDT"})
        # Execute the real native conversion/shift helper with no wallet/exchange.
        native = object.__new__(Backtesting)
        native.strategy = strategy
        native.abort = False
        native._set_progress_step = lambda *a: None
        native._increment_progress = lambda: None
        native.dataprovider = SimpleNamespace(_set_cached_df=lambda *a: None)
        native.config = {"candle_type_def": CandleType.SPOT}
        native.timeframe = "5m"
        native.required_startup = strategy.startup_candle_count
        native.timerange = TimeRange.parse_timerange("20260109-20260110")
        rows = native._get_ohlcv_as_lists({"BTC/USDT": indicators})["BTC/USDT"]
        entries = [r[0] for r in rows if r[HEADERS.index("enter_long")]]
        self.assertEqual(entries, [pd.Timestamp("2026-01-09 00:05", tz="UTC")])

    def test_f2_retains_upstream_indicator_and_signal_outputs(self):
        frame = candles("4h", 500, growth=0)
        outputs = []
        for name in ("AverageStrategy", "AverageRD1ControlNative", "F2Volatility24Native"):
            strategy = resolved(name)
            df = strategy.populate_indicators(frame.copy(), {"pair": "BTC/USDT"})
            outputs.append(strategy.ft_advise_signals(df, {"pair": "BTC/USDT"}))
        for out in outputs[1:]:
            pd.testing.assert_frame_equal(outputs[0], out)

    def test_scale_rolls_at_0005_and_minimum_cannot_enlarge_stake(self):
        h = candles()
        for day in ("2026-01-09", "2026-01-10"):
            at = pd.Timestamp(day, tz="UTC")
            old = daily_features(h, pd.DatetimeIndex([at-pd.Timedelta(days=1)])).iloc[0].scale24
            new = daily_features(h, pd.DatetimeIndex([at])).iloc[0].scale24
            self.assertEqual(effective_daily_scale(h, at), old)
            self.assertEqual(effective_daily_scale(h, at+pd.Timedelta(minutes=5)), new)
        self.assertEqual(scale_risk_approved_stake(90, .05, 5, 100), 0)
        self.assertEqual(scale_risk_approved_stake(90, .5, 5, 100), 45)
        self.assertEqual(scale_risk_approved_stake(90, 2, 5, 100), 0)

    def test_unfinished_adapter_refuses_financial_activation_not_zero_return(self):
        for name in ("F1Momentum168Native", "AverageRD1ControlNative", "F2Volatility24Native"):
            strategy = resolved(name)
            with self.assertRaisesRegex(OperationalException, "RD1 lifecycle"):
                strategy.bot_start()
            self.assertFalse(strategy.confirm_trade_entry())
            self.assertEqual(strategy.custom_stake_amount(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
