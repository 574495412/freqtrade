"""Native precedence, funding boundaries, fixed stop, timeout and causal precision checks."""

from types import SimpleNamespace

import pandas as pd
import pytest

from research.crypto.first_hit_paths import causal_ticks, paths
from research.crypto.replay_first_hit_native import replay
from research.crypto.strategies.first_hit_dual import FirstHitDualBase


def fixture(side, case):
    clocks = pd.date_range("2024-01-01", periods=55, freq="5min", tz="UTC")
    prices = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0}, index=clocks)
    prices.index.name = "date"
    if case in {"profit_open", "profit_then_adverse", "gap_stop"}:
        rate = 100 + side * (-2 if case == "gap_stop" else 1)
        prices.loc[clocks[3], ["open", "high", "low", "close"]] = [
            rate,
            rate + 0.1,
            rate - 0.1,
            rate,
        ]
    if case in {"both_barriers", "profit_then_adverse"}:
        offset = 2 if case == "both_barriers" else 3
        prices.loc[clocks[offset], ["high", "low"]] = [103.0, 97.0]
    if case == "high_only":
        prices.loc[clocks[2], "high" if side == 1 else "low"] = 103.0 if side == 1 else 97.0
    if case == "time_priority":
        rate = 100 + side
        prices.loc[clocks[50], ["open", "high", "low", "close"]] = [rate, 103, 97, rate]
    prices["known_tick"] = causal_ticks(prices)
    funding = pd.DataFrame(
        {"funding_rate": [-side * 0.01 if case == "funding_profit" else 0.0]}, index=[clocks[3]]
    )
    funding.index.name = "date"
    marks = pd.Series(100.0, index=clocks, name="open")
    marks.index.name = "date"
    unit = SimpleNamespace(row_id=1, feature_open=clocks[0], sleeve="small", side=side, atr=0.05)
    return unit, prices, funding, marks


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("cost", [0, 3, 10])
@pytest.mark.parametrize(
    "case,reason",
    [
        ("both_barriers", "stop_loss"),
        ("high_only", "first_hit_time"),
        ("profit_open", "first_hit_profit"),
        ("profit_then_adverse", "first_hit_profit"),
        ("gap_stop", "first_hit_gap"),
        ("funding_profit", "first_hit_profit"),
        ("timeout", "first_hit_time"),
        ("time_priority", "first_hit_time"),
    ],
)
def test_vector_path_matches_native_exit_order_and_decimal_cash(side, cost, case, reason):
    unit, price, fund, mark = fixture(side, case)
    actual = paths(pd.DataFrame([vars(unit)]), price, fund, mark, cost).iloc[0]
    native = replay(unit, price, fund, mark, cost)
    assert actual.exit_reason == native["exit_reason"] == reason
    assert actual.exit_time == native["exit_time"]
    assert actual.exit_price == native["exit_price"]
    assert actual.net_label_3bps == pytest.approx(native["net_label"], abs=1e-12)
    assert actual.locked_stop == native["locked_stop"]


def test_future_price_digits_cannot_change_prior_precision():
    _, price, _, _ = fixture(1, "timeout")
    before = causal_ticks(price.iloc[:20])
    changed = price.copy()
    changed.loc[changed.index[20] :, "high"] = 100.123456
    after = causal_ticks(changed).iloc[:20]
    pd.testing.assert_series_equal(before, after, check_exact=True)
    assert after.iloc[-1] == 0.1


def test_incomplete_paths_are_rejected_instead_of_marked_as_winners():
    unit, price, fund, mark = fixture(1, "timeout")
    with pytest.raises(ValueError, match="not yet mature"):
        paths(pd.DataFrame([vars(unit)]), price.iloc[:20], fund, mark)


@pytest.mark.parametrize("side", [1, -1])
def test_core_finite_path_uses_24h_and_native_stopstate(side):
    unit, price, fund, mark = fixture(side, "timeout")
    clocks = pd.date_range(price.index[0], periods=300, freq="5min", tz="UTC", name="date")
    price = price.reindex(clocks).ffill()
    price["known_tick"] = causal_ticks(price)
    mark = mark.reindex(clocks).ffill()
    unit.sleeve = "core"
    actual = paths(pd.DataFrame([vars(unit)]), price, fund, mark).iloc[0]
    native = replay(unit, price, fund, mark)
    assert actual.exit_reason == native["exit_reason"] == "first_hit_time"
    assert actual.exit_time == actual.entry + pd.Timedelta(hours=24)
    assert actual.exit_time == native["exit_time"]
    assert actual.net_label_3bps == pytest.approx(native["net_label"], abs=1e-12)


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize(
    "tag,risk,cap", [("small_first_hit", 0.99, 39.6), ("core_first_hit", 4.95, 198)]
)
def test_sizing_preserves_fixed_initial_risk_for_each_layer(side, tag, risk, cap):
    instance = FirstHitDualBase({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    instance.closed_row = lambda *args: SimpleNamespace(feature_atr=0.3)
    stake = instance.custom_stake_amount("ETH/USDT:USDT", None, 100, 500, 1, 1000, 2, tag, side)
    distance = instance.distance(instance.closed_row(None, None), 100, tag)
    sign = 1 if side == "long" else -1
    loss = stake * 2 * (distance + 0.0008 * (2 - sign * distance))
    assert 0 < loss <= risk and stake <= cap
    assert (
        instance.custom_stake_amount("ETH/USDT:USDT", None, 100, 500, cap + 1, 1000, 2, tag, side)
        == 0
    )


def test_guard_rejects_unknown_tags_and_non_backtest_mode():
    instance = FirstHitDualBase({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    instance.dp = SimpleNamespace(runmode=SimpleNamespace(value="live"))
    with pytest.raises(ValueError, match="Unqualified"):
        instance.populate_indicators(None, {"pair": "ETH/USDT:USDT"})
    assert not instance.confirm_trade_entry(
        "ETH/USDT:USDT", "limit", 1, 100, "GTC", None, "force_entry", "long"
    )
