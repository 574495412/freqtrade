"""Risk invariants that differ from the source NFI's unbounded lifecycle."""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user_data/crypto_quant/external_sources_20261022/nfi_pre2024"))
sys.path.insert(0, str(ROOT / "research/crypto/strategies"))
from nfi_risk_cap import KEY, NFIRiskCapNoDCA, NFIRiskCapOneX, stop_risk  # noqa: E402
from NostalgiaForInfinityX4 import NostalgiaForInfinityX4  # noqa: E402

from freqtrade.enums import ExitType  # noqa: E402
from freqtrade.optimize.backtesting import Backtesting  # noqa: E402
from freqtrade.strategy.interface import ExitCheckTuple  # noqa: E402


NOW = datetime(2026, 10, 24, tzinfo=UTC)


def strategy(monkeypatch, kind=NFIRiskCapOneX):
    value = kind.__new__(kind)
    value.config = {"research_initial_equity_usdt": 1000, "fee": 0.0008}
    value.remaining_capacity = lambda: (80.0, 800.0)
    value._remove_profit_target = lambda pair: None
    monkeypatch.setattr(NostalgiaForInfinityX4, "adjust_trade_position", lambda *a, **k: 999.0)
    return value


def position():
    state = {
        "absolute_stop": 88.0,
        "first_price": 100.0,
        "first_stake": 40.0,
        "risk_budget": 20.0,
        "margin_cap": 200.0,
        "last_add_clock": None,
    }
    values = {KEY: state}
    trade = SimpleNamespace(
        amount=0.4,
        open_rate=100.0,
        stop_loss=88.0,
        fee_open=0.0008,
        fee_close=0.0008,
        realized_profit=0.0,
        funding_fees=0.0,
        stake_amount=40.0,
        max_stake_amount=40.0,
        has_open_orders=False,
        nr_of_successful_entries=1,
        nr_of_successful_exits=0,
        leverage=1.0,
        is_short=False,
        entry_side="buy",
        open_date_utc=NOW,
        get_custom_data=lambda key: values.get(key),
        set_custom_data=lambda key, data: values.update({key: data}),
    )
    return trade, state, values


def adjust(s, trade, when=NOW):
    return s.adjust_trade_position(trade, when, 96.0, -0.04, 1.0, 999.0, 96.0, 96.0, -0.04, -0.04)


def test_equal_initial_sizing_preserves_a_fee_inclusive_three_entry_reserve(monkeypatch):
    monkeypatch.setattr(NostalgiaForInfinityX4, "custom_stake_amount", lambda *a, **k: 100.0)
    sizes = [
        strategy(monkeypatch, kind).custom_stake_amount(
            "ETH/USDT:USDT", NOW, 100.0, 100.0, 1.0, 999.0, 1.0, "61", "long"
        )
        for kind in [NFIRiskCapOneX, NFIRiskCapNoDCA]
    ]
    assert sizes[0] == sizes[1]
    planned = Decimal(str(sizes[0])) * 3 * (Decimal("0.12") + Decimal("0.0008") * Decimal("1.88"))
    assert planned <= Decimal("17.500000000001")


def test_additions_are_not_martingale_and_only_once_per_closed_candle(monkeypatch):
    s = strategy(monkeypatch)
    trade, state, _ = position()
    assert adjust(s, trade)[0] <= state["first_stake"]
    assert adjust(s, trade) is None
    assert adjust(s, trade, NOW + timedelta(minutes=5))[0] <= state["first_stake"]
    trade.nr_of_successful_entries = 3
    assert adjust(s, trade, NOW + timedelta(minutes=10)) is None


@pytest.mark.parametrize("change", ["partial_exit", "funding", "margin", "unknown_state"])
def test_exhausted_or_uncertain_campaign_cannot_add(monkeypatch, change):
    s = strategy(monkeypatch)
    trade, state, values = position()
    if change == "partial_exit":
        trade.nr_of_successful_exits = 1
    elif change == "funding":
        trade.funding_fees = -30
    elif change == "margin":
        trade.max_stake_amount = state["margin_cap"]
    else:
        values.clear()
    assert adjust(s, trade) is None


def test_no_dca_control_still_permits_source_reductions(monkeypatch):
    s = strategy(monkeypatch, NFIRiskCapNoDCA)
    trade, _, _ = position()
    assert adjust(s, trade) is None
    monkeypatch.setattr(NostalgiaForInfinityX4, "adjust_trade_position", lambda *a, **k: -999.0)
    assert adjust(s, trade)[0] == -trade.stake_amount


def test_stop_after_fill_does_not_widen_and_risk_exit_is_never_vetoed(monkeypatch):
    s = strategy(monkeypatch)
    trade, _, _ = position()
    trade.stop_loss = 92.0
    stop = s.custom_stoploss("ETH/USDT:USDT", trade, NOW, 100, 0, True)
    assert stop == pytest.approx(0.08)
    for reason in ["stop_loss", "trailing_stop_loss", "risk_cap_time_limit", "force_exit"]:
        assert s.confirm_trade_exit("ETH/USDT:USDT", trade, "market", 0.4, 92, "gtc", reason, NOW)
    assert (
        s.custom_exit("ETH/USDT:USDT", trade, NOW + timedelta(days=14), 100, 0)
        == "risk_cap_time_limit"
    )


def test_known_funding_breach_and_lost_state_trigger_full_exit(monkeypatch):
    s = strategy(monkeypatch)
    trade, _, values = position()
    trade.funding_fees = -21
    assert s.custom_exit("ETH/USDT:USDT", trade, NOW, 100, 0) == "risk_cap_budget_breach"
    values.clear()
    assert s.custom_exit("ETH/USDT:USDT", trade, NOW, 100, 0) == "risk_cap_missing_state"


def test_gap_loss_is_disclosed_separately_from_planned_stop_risk():
    trade, state, _ = position()
    planned = stop_risk(trade, state)
    gap_loss = trade.amount * (100 - 40 + 100 * trade.fee_open + 40 * trade.fee_close)
    assert gap_loss > planned
    assert gap_loss > state["risk_budget"]


@pytest.mark.parametrize("gap", [0.15, 0.30, 0.50])
def test_native_matcher_uses_gap_open_instead_of_fictional_stop_price(gap):
    trade, _, _ = position()
    trade.liquidation_price = None
    opened = 100 * (1 - gap)
    row = (NOW, opened, opened + 0.1, opened - 1, opened, 0, 0, 0, 0, "", "")
    result = Backtesting._get_close_rate_for_stoploss(
        SimpleNamespace(), row, trade, ExitCheckTuple(ExitType.STOP_LOSS), 60
    )
    assert result == opened
    assert result < trade.stop_loss
