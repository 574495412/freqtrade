from types import SimpleNamespace

import pandas as pd
import pytest

from research.crypto.prepare_signal_lease import expand
from research.crypto.replay_signal_lease import replay
from research.crypto.strategies.signal_lease import cycle_history, eligible_leases
from tests.research.test_first_hit_paths import fixture


def parents():
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-08-01T00:05Z"),
                "symbol": "ETHUSDT",
                "row_id": 10,
                "forecast_sleeve": "core",
                "forecast_side": 1,
                "feature_atr": 1.0,
            },
            {
                "date": pd.Timestamp("2024-08-01T04:05Z"),
                "symbol": "ETHUSDT",
                "row_id": 11,
                "forecast_sleeve": "core",
                "forecast_side": -1,
                "feature_atr": 2.0,
            },
        ]
    )


def test_parent_lease_clock_priority_expiry_and_future_prefix():
    source = parents()
    frame = expand(source)
    assert frame.iloc[0].forecast_sleeve == "core"
    first_child = frame.loc[frame.forecast_sleeve.eq("small")].iloc[0]
    assert first_child.date == source.date.iloc[0] + pd.Timedelta(minutes=5)
    assert first_child.lease_id == "ETHUSDT:10"
    final = frame.iloc[-1]
    assert final.date == source.date.iloc[-1] + pd.Timedelta(hours=20)
    assert final.lease_id == "ETHUSDT:11"
    cut = source.date.iloc[-1]
    pd.testing.assert_frame_equal(
        frame.loc[frame.date < cut].reset_index(drop=True),
        expand(source.iloc[:1]).loc[lambda f: f.date < cut].reset_index(drop=True),
    )
    assert frame.loc[frame.date.eq(cut)].iloc[0].forecast_sleeve == "core"


def closed(lease, when, pnl):
    return SimpleNamespace(
        enter_tag="small_first_hit",
        get_custom_data=lambda key: lease,
        is_open=False,
        close_profit_abs=pnl,
        close_date_utc=when,
    )


def test_only_profitable_complete_cycles_recycle_after_a_full_flat_bar():
    now = pd.Timestamp("2024-08-01T01:00Z")
    pool = pd.DataFrame({"lease_id": ["good", "bad", "zero"]})
    history = cycle_history(
        [closed("good", now, 1), closed("bad", now, -0.01), closed("zero", now, 0)]
    )
    assert eligible_leases(pool, history, now + pd.Timedelta(minutes=5), 4).empty
    assert eligible_leases(pool, history, now + pd.Timedelta(minutes=10), 4).lease_id.tolist() == [
        "good"
    ]
    assert eligible_leases(pool, history, now + pd.Timedelta(minutes=10), 1).empty
    history["good"]["entries"] = 4
    assert eligible_leases(pool, history, now + pd.Timedelta(minutes=10), 4).empty


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("cost", [0, 3, 10])
@pytest.mark.parametrize("open_observed", [True, False])
def test_core_profit_lock_uses_only_observed_opens_with_protected_native_callback(
    side, cost, open_observed
):
    unit, price, fund, mark = fixture(side, "timeout")
    clock = pd.date_range(price.index[0], periods=300, freq="5min", tz="UTC", name="date")
    price, mark = price.reindex(clock).ffill(), mark.reindex(clock).ffill()
    unit.sleeve, unit.protect_core = "core", True
    if open_observed:
        for index, move in [(3, 2.5), (4, 0.7)]:
            rate = 100 + side * move
            price.loc[clock[index], ["open", "high", "low", "close"]] = [
                rate,
                rate + 0.05,
                rate - 0.05,
                rate,
            ]
    else:
        price.loc[clock[3], "high" if side == 1 else "low"] = 100 + side * 2.5
    result = replay(unit, price, fund, mark, cost)
    if open_observed:
        assert result["exit_reason"] == "core_profit_lock"
        assert result["exit_time"] == clock[4]
        assert result["net_label"] > 0
    else:
        assert result["exit_reason"] == "first_hit_time"
        assert result["exit_time"] == clock[290]


@pytest.mark.parametrize(
    "name", ["LeaseOnce", "LeaseRecycle", "LeaseOnceProtect", "LeaseRecycleProtect"]
)
@pytest.mark.parametrize("side", [1, -1])
def test_lease_metadata_and_quarter_r_stop_survive_real_callback_protection(
    monkeypatch, name, side
):
    from freqtrade.enums import TradingMode
    from freqtrade.exchange.exchange import TICK_SIZE
    from freqtrade.persistence import LocalTrade
    from freqtrade.persistence.custom_data import CustomDataWrapper
    from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
    from research.crypto.strategies import signal_lease

    monkeypatch.setattr(CustomDataWrapper, "use_db", False)
    monkeypatch.setattr(CustomDataWrapper, "custom_data", [])
    strategy = getattr(signal_lease, name)({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    strategy.minimal_roi = {0: 1000.0}
    strategy.closed_row = lambda *args: SimpleNamespace(feature_atr=0.05, lease_id="ETHUSDT:7")
    clock = pd.Timestamp("2024-08-01T00:15Z").to_pydatetime()
    trade = LocalTrade(
        id=777,
        pair="ETH/USDT:USDT",
        open_date=clock,
        open_rate=100,
        amount=0.01,
        stake_amount=0.5,
        leverage=2,
        fee_open=0.0008,
        fee_close=0.0008,
        is_short=side == -1,
        trading_mode=TradingMode.FUTURES,
        price_precision=0.1,
        precision_mode_price=TICK_SIZE,
        enter_tag="small_first_hit",
    )
    trade.adjust_stop_loss(100, strategy.stoploss, initial=True)
    strategy_safe_wrapper(strategy.order_filled)(
        pair=trade.pair,
        trade=trade,
        order=SimpleNamespace(ft_order_side=trade.entry_side),
        current_time=clock,
    )
    strategy._ft_stop_uses_after_fill = True
    strategy.ft_stoploss_adjust(100, trade, clock, trade.calc_profit_ratio(100), 0, after_fill=True)
    assert trade.get_custom_data("lease_id") == "ETHUSDT:7"
    expected = 0.25 * (0.008 + 0.0008 * (2 - side * 0.008)) * 2 / (1 + side * 0.0008)
    assert trade.get_custom_data("target_roi") == pytest.approx(expected, abs=1e-15)
    assert trade.stop_loss == pytest.approx(100 - side * 0.8)
    exits = strategy.should_exit(trade, 100, clock, enter=False, exit_=False, low=97, high=103)
    assert exits[0].exit_reason == "stop_loss" and not trade.is_stop_loss_trailing
