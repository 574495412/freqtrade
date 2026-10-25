"""Protected native callback parity for small intrabar profit, adverse bars and funding changes."""

import pandas as pd
import pytest

from research.crypto.intrabar_small_paths import paths
from research.crypto.replay_intrabar_small import replay
from tests.research.test_first_hit_paths import fixture


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("cost", [0, 3, 10])
@pytest.mark.parametrize(
    "case,expected",
    [
        ("both_barriers", "stop_loss"),
        ("high_only", "roi"),
        ("profit_open", "first_hit_profit"),
        ("profit_then_adverse", "first_hit_profit"),
        ("gap_stop", "first_hit_gap"),
        ("funding_profit", "first_hit_profit"),
        ("timeout", "first_hit_time"),
        ("time_priority", "first_hit_time"),
    ],
)
def test_intrabar_kernel_matches_protected_native_methods(side, cost, case, expected):
    unit, price, fund, mark = fixture(side, case)
    actual = paths(pd.DataFrame([vars(unit)]), price, fund, mark, cost).iloc[0]
    native = replay(unit, price, fund, mark, cost)
    assert actual.exit_reason == native["exit_reason"] == expected
    assert actual.exit_time == native["exit_time"]
    assert actual.exit_price == native["exit_price"]
    assert actual.locked_stop == native["locked_stop"]
    assert actual.net_label_3bps == pytest.approx(native["net_label"], abs=1e-12)


@pytest.mark.parametrize("side", [1, -1])
def test_current_funding_changes_the_net_profit_quote_before_intrabar_execution(side):
    unit, price, fund, mark = fixture(side, "timeout")
    clock = price.index
    fund.iloc[0, 0] = side * 0.001
    between_quotes = 0.55 if side == 1 else 0.45
    price.loc[clock[3], "high" if side == 1 else "low"] = 100 + side * between_quotes
    price.loc[clock[4], "high" if side == 1 else "low"] = 100 + side * 0.8
    actual = paths(pd.DataFrame([vars(unit)]), price, fund, mark).iloc[0]
    native = replay(unit, price, fund, mark)
    assert actual.exit_reason == native["exit_reason"] == "roi"
    assert actual.exit_time == native["exit_time"] == clock[4]
    assert actual.funding_credit == pytest.approx(-0.001)
    assert actual.net_label_3bps == pytest.approx(native["net_label"], abs=1e-12)


@pytest.mark.parametrize("side", [1, -1])
def test_core_checkpoint_path_is_unchanged(side):
    from research.crypto.first_hit_paths import causal_ticks
    from research.crypto.first_hit_paths import paths as original

    unit, price, fund, mark = fixture(side, "timeout")
    clock = pd.date_range(price.index[0], periods=300, freq="5min", tz="UTC", name="date")
    price = price.reindex(clock).ffill()
    price["known_tick"] = causal_ticks(price)
    mark = mark.reindex(clock).ffill()
    unit.sleeve = "core"
    frame = pd.DataFrame([vars(unit)])
    pd.testing.assert_frame_equal(
        paths(frame, price, fund, mark), original(frame, price, fund, mark), check_exact=True
    )
    actual = paths(frame, price, fund, mark).iloc[0]
    assert replay(unit, price, fund, mark)["exit_time"] == actual.exit_time
