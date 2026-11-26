# ruff: noqa: S101 - pytest assertions
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from research.crypto.skew_vol_native_20261113_v1.features import monthly_targets, observations
from research.crypto.skew_vol_native_20261113_v1.SkewVolPortfolio import AnnualSkew


def panel():
    dates = pd.date_range("2023-01-01", periods=800, tz="UTC")
    returns = np.random.default_rng(13).normal(0.0001, 0.02, 800)
    return pd.DataFrame({"date": dates, "close": 100 * np.exp(np.cumsum(returns))})


def test_exact_prefix_and_quote_unit_invariance():
    frame = panel()
    edge = pd.Timestamp("2024-06-01T00:00Z")
    full = observations(frame, edge)
    prefix = observations(frame.loc[frame.date < edge], edge)
    assert full == prefix
    frame.loc[frame.date >= edge, "close"] *= 200
    assert observations(frame, edge) == full
    frame["close"] *= 0.001
    assert observations(frame, edge)["skew360"] == pytest.approx(full["skew360"])


def test_missing_price_is_not_filled_or_compressed():
    frame = panel()
    edge = pd.Timestamp("2024-06-01T00:00Z")
    frame = frame.loc[frame.date.ne(edge - pd.Timedelta(days=10))]
    with pytest.raises(ValueError):
        observations(frame, edge)


def test_cross_section_keeps_exact_quartiles_and_unknown_fails():
    rows = [{"pair": str(i), "skew360": float(i)} for i in range(40)]
    targets = monthly_targets(rows, "skew360")
    assert list(targets.values()).count(1) == 10
    assert list(targets.values()).count(-1) == 10
    with pytest.raises(ValueError):
        monthly_targets(rows[:-1], "skew360")


def test_native_signal_shift_places_exit_day1_and_entry_day2():
    obj = AnnualSkew({"stake_currency": "USDT"})
    obj._scores = {"months": {"2025-01": {"skew360": {"BTC/USDT:USDT": 1}}}}
    data = pd.DataFrame(
        {"date": pd.date_range("2024-12-31", periods=3, tz="UTC"), "volume": [1, 1, 1]}
    )
    frame = obj.populate_exit_trend(obj.populate_entry_trend(data, {"pair": "BTC/USDT:USDT"}), {})
    assert frame.enter_long.tolist() == [0, 1, 0]
    assert frame.exit_long.tolist() == [1, 0, 0]
    assert not (frame.enter_long.eq(1) & frame.exit_long.eq(1)).any()


def test_native_minimum_order_does_not_upsize_sleeve():
    obj = AnnualSkew({"stake_currency": "USDT"})
    obj.wallets = SimpleNamespace(get_total_stake_amount=lambda: 495)
    obj._budgets, obj._intents = {}, set()
    obj.research_trace = {"monthly_budgets": {}, "attempts": {}, "accepted_intents": []}
    kwargs = {
        "pair": "BTC/USDT:USDT",
        "current_time": pd.Timestamp("2025-01-02T00:00Z"),
        "current_rate": 100000,
        "proposed_stake": 100,
        "min_stake": 100,
        "max_stake": 495,
        "leverage": 1,
        "entry_tag": None,
        "side": "long",
    }
    assert obj.custom_stake_amount(**kwargs) == 0
    kwargs.update(pair="ETH/USDT:USDT", min_stake=10)
    assert obj.custom_stake_amount(**kwargs) == 24.75
