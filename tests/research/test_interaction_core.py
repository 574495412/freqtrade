"""Fixed-risk and lifecycle checks before any core-component account outcomes."""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research/crypto/strategies"))
from interaction_core import InteractionCoreBase

from freqtrade.persistence import Trade


def strategy():
    instance = object.__new__(InteractionCoreBase)
    instance.config = {"research_initial_equity_usdt": 1000, "fee": 0.0008}
    instance.dp = SimpleNamespace(runmode=SimpleNamespace(value="backtest"))
    instance.closed_row = lambda *args: SimpleNamespace(feature_atr=1.0)
    return instance


@pytest.mark.parametrize("side", ["long", "short"])
def test_actual_directional_fee_sizing_stays_inside_fixed_dollar_risk(side):
    instance = strategy()
    value = instance.custom_stake_amount(
        "ETH/USDT:USDT", None, 100.0, 500.0, 1.0, 1000.0, 2.0, "core_interaction", side
    )
    sign = 1 if side == "long" else -1
    q = Decimal(str(value)) * 2 / 100
    stop = Decimal(100) * (1 - Decimal(sign) * Decimal(".06"))
    loss = q * (-Decimal(sign) * (stop - 100) + Decimal(".0008") * (100 + stop))
    assert loss <= Decimal("4.95")
    assert value <= 198
    assert (
        instance.custom_stake_amount(
            "ETH/USDT:USDT", None, 100.0, 500.0, 1.0, 5.0, 2.0, "core_interaction", side
        )
        == 5
    )


def test_invalid_signal_or_risk_state_cannot_allocate_capital():
    instance = strategy()
    assert (
        instance.custom_stake_amount(
            "ETH/USDT:USDT", None, 100.0, 500.0, 1.0, 1000.0, 2.0, "force_entry", "long"
        )
        == 0
    )
    instance.closed_row = lambda *args: SimpleNamespace(feature_atr=10.0)
    assert (
        instance.custom_stake_amount(
            "ETH/USDT:USDT", None, 100.0, 500.0, 1.0, 1000.0, 2.0, "core_interaction", "long"
        )
        == 0
    )
    assert instance.position_adjustment_enable is False


def test_first_fill_locks_stop_and_full_exit_time_does_not_depend_on_pnl():
    instance, state = strategy(), {}
    start = datetime(2024, 7, 1, tzinfo=UTC)
    trade = SimpleNamespace(
        entry_side="buy",
        is_short=False,
        open_date_utc=start,
        open_rate=100.0,
        enter_tag="core_interaction",
        set_custom_data=lambda k, v: state.update({k: v}),
        get_custom_data=lambda k: state.get(k),
    )
    instance.order_filled("ETH/USDT:USDT", trade, SimpleNamespace(ft_order_side="buy"), start)
    assert state["initial_stop"] == pytest.approx(94.0)
    for profit in [-0.5, 0.5]:
        assert (
            instance.custom_exit("ETH/USDT:USDT", trade, start + timedelta(hours=24), 100.0, profit)
            == "core_interaction_24h"
        )
    state.clear()
    assert (
        instance.custom_exit("ETH/USDT:USDT", trade, start, 100.0, 0)
        == "missing_core_interaction_state"
    )


def test_entry_capacity_and_non_backtest_modes_are_rejected(monkeypatch):
    instance = strategy()
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kwargs: [object(), object()])
    args = ("ETH/USDT:USDT", "limit", 1.0, 100.0, "GTC", None, "core_interaction", "long")
    assert not instance.confirm_trade_entry(*args)
    instance.dp.runmode.value = "live"
    assert not instance.confirm_trade_entry(*args)
    with pytest.raises(ValueError, match="unqualified"):
        instance.populate_indicators(None, {"pair": "ETH/USDT:USDT"})
