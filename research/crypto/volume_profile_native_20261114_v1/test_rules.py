"""Rule, publication-clock, source state and native hook checks; no strategy PnL."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap import boot, HERE, ROOT

boot()
from ProfileRules import FlatState, daily_profile, features
from ProfilePortfolio import ProfileBreakout
from freqtrade.optimize.volume_profile import VolumeProfileBacktesting
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade, Trade
from freqtrade.enums import RunMode


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_invalid_volumes_reject(bad):
    with pytest.raises(ValueError):
        daily_profile([3, 4], [1, 2], [bad, 1])


def source_module(name, file):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "research/crypto/mechanism_source_intake_20261114_v1/sources/profile" / file
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def test_histograms_mass_ties_and_flat_bin_match_pure_source():
    m = source_module("profile_indicator_reference", "indicadores.py")
    for highs, lows, vol in [
        ([2, 3, 4], [1, 2, 2], [1, 2, 3]),
        ([2, 2, 2], [1, 1, 1], [1, 1, 1]),
        ([4, 2, 3], [1, 2, 2], [5, 7, 1]),
    ]:
        actual = daily_profile(highs, lows, vol, bins=4, coverage=0.7)
        edges, mass = m.VolumeProfile.perfil_de_volume(
            np.array(highs), np.array(lows), np.array(vol), 4
        )
        poc, vah, val = m.VolumeProfile.calcular_value_area(edges, mass, 70)
        np.testing.assert_array_equal(actual["weights"], mass)
        assert actual["weights"].sum() == pytest.approx(sum(vol))
        assert (actual["poc"], actual["vah"], actual["val"]) == (poc, vah, val)


def row(close, volume=20, high=None, low=None):
    return dict(
        close=close,
        volume=volume,
        high=high or close + 0.2,
        low=low or close - 0.2,
        poc=10,
        vah=11,
        val=9,
        volume_mean=10,
    )


@pytest.mark.parametrize("kind", ["fade", "breakout", "no_volume"])
def test_flat_state_sequences_match_source_pure_decisions(kind):
    m = source_module("profile_state_reference", "strategy.py")
    mine = FlatState(kind)
    theirs = (
        m.EstrategiaFade(0.055, 100, "RANGE_EXTREME", 32)
        if kind == "fade"
        else m.EstrategiaBreakout(0.055, 100, "RANGE_EXTREME", 1, 0 if kind == "no_volume" else 1.5)
    )
    inputs = [
        row(x, v)
        for x, v in [
            (10, 20),
            (12, 10),
            (12.5, 20),
            (10.5, 20),
            (8, 20),
            (8.5, 20),
            (9.5, 20),
            (11.5, 20),
            (10, 20),
        ]
    ]
    inputs += [row(12)] * 34 + [row(10)]
    for r in inputs:
        got = mine.step(r)
        if kind == "fade":
            expected = theirs.processar_vela(
                r["high"], r["low"], r["close"], r["vah"], r["val"], r["poc"]
            )
        else:
            expected = theirs.processar_vela(
                r["close"], r["vah"], r["val"], r["poc"], r["volume"], r["volume_mean"]
            )
        assert (got is not None) == (expected is not None)
        if got:
            assert got == {
                "side": 1 if expected == "COMPRAR" else -1,
                "stop": theirs.sl_pendente,
                "target": theirs.tp_pendente,
            }


def test_hook_default_off_delegates_exact_args():
    a = object.__new__(VolumeProfileBacktesting)
    a.strategy = SimpleNamespace()
    with patch.object(Backtesting, "check_for_trade_entry", return_value="native") as base:
        assert a.check_for_trade_entry((1, 2)) == "native"
        base.assert_called_once_with((1, 2))


@pytest.mark.parametrize("bad", ["live", "pair", "clock"])
def test_hook_rejects_unsupported_context(bad):
    a = object.__new__(VolumeProfileBacktesting)
    a.strategy = SimpleNamespace(native_profile_step=lambda *args: None)
    a.config = {
        "runmode": RunMode.BACKTEST,
        "dry_run": True,
        "exchange": {"pair_whitelist": ["BTC/USDT:USDT"]},
    }
    a.timeframe = "15m"
    if bad == "live":
        a.config["runmode"] = RunMode.LIVE
    if bad == "pair":
        a.config["exchange"]["pair_whitelist"] = ["ETH/USDT:USDT"]
    if bad == "clock":
        a.timeframe = "1h"
    old = Trade.use_db
    Trade.use_db = False
    try:
        with pytest.raises(ValueError):
            a.check_for_trade_entry((pd.Timestamp("2025-01-01T01:00Z"),))
    finally:
        Trade.use_db = old


def test_hook_classifies_before_native_detail_cache_and_uses_held_state():
    a = object.__new__(VolumeProfileBacktesting)
    seen = []
    a.strategy = SimpleNamespace(
        native_profile_step=lambda p, t, h, c: seen.append(h) or ("long" if not h else None)
    )
    a.config = {
        "runmode": RunMode.BACKTEST,
        "dry_run": True,
        "exchange": {"pair_whitelist": ["BTC/USDT:USDT"]},
    }
    a.timeframe = "15m"
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    try:
        assert a.check_for_trade_entry((pd.Timestamp("2025-01-01T01:00Z"),)) == "long"
        LocalTrade.bt_trades_open_pp["BTC/USDT:USDT"] = [object()]
        assert a.check_for_trade_entry((pd.Timestamp("2025-01-01T01:15Z"),)) is None
        assert seen == [False, True]
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = old


def test_entry_gap_risk_sizing_and_no_minimum_upsizing():
    s = object.__new__(ProfileBreakout)
    clock = pd.Timestamp("2025-01-01T00:15Z")
    s._pending = {"clock": clock, "side": 1, "stop": 90.0, "target": 120.0}
    s.wallets = SimpleNamespace(
        get_total=lambda currency: 1000.0, get_available_stake_amount=lambda: 990.0
    )
    s.research_trace = {"sizing": []}
    args = dict(
        pair="BTC/USDT:USDT",
        current_time=clock,
        current_rate=100.0,
        proposed_stake=990.0,
        min_stake=1.0,
        max_stake=990.0,
        leverage=3.0,
        entry_tag=None,
        side="long",
    )
    assert s.custom_stake_amount(**args) == pytest.approx(100 / 3)
    args["min_stake"] = 100
    assert s.custom_stake_amount(**args) == 0
    args["current_rate"] = 89
    assert s.custom_stake_amount(**args) == 0
