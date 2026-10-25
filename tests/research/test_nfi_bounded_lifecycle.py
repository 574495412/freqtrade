"""Whole-exit semantics and invariant campaign risk for the NFI lifecycle experiment."""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user_data/crypto_quant/external_sources_20261022/nfi_pre2024"))
sys.path.insert(0, str(ROOT / "research/crypto/strategies"))
from nfi_bounded_lifecycle import NFIBoundedDualExit, NFIBoundedFullExit, NFIBoundedPartial, layer  # noqa: E402
from NostalgiaForInfinityX4 import NostalgiaForInfinityX4  # noqa: E402


def trade(net=1):
    state = {
        "sleeve": "small",
        "initial_stop": 94.0,
        "first_price": 100.0,
        "first_stake": 2.5,
        "risk_budget": 0.99,
        "margin_cap": 19.8,
    }
    return SimpleNamespace(
        stake_amount=2.5,
        amount=0.05,
        open_rate=100.0,
        fee_open=0.0008,
        fee_close=0.0008,
        leverage=2.0,
        realized_profit=0.0,
        funding_fees=0.0,
        has_open_orders=False,
        nr_of_successful_entries=1,
        nr_of_successful_exits=0,
        is_short=False,
        get_custom_data=lambda key: state,
        calculate_profit=lambda rate: SimpleNamespace(total_profit=net),
    ), state


def adjust(strategy, position):
    return strategy.adjust_trade_position(
        position,
        datetime(2024, 1, 1, tzinfo=UTC),
        96.0,
        -0.08,
        1.0,
        100.0,
        96.0,
        96.0,
        -0.08,
        -0.08,
    )


def test_only_pure_rebuy_tags_are_core_and_force_entries_are_rejected():
    assert layer("61 ") == "core"
    assert layer("61 102 ") == "small"
    assert layer("1 4 6 ") == "small"
    assert layer("force_entry") is None
    assert layer(None) is None


def test_full_reduction_uses_owned_stake_even_when_the_whole_position_is_losing(monkeypatch):
    monkeypatch.setattr(NostalgiaForInfinityX4, "adjust_trade_position", lambda *a, **k: -1.0)
    position, _ = trade(net=-1.0)
    strategy = NFIBoundedFullExit.__new__(NFIBoundedFullExit)
    amount, tag = adjust(strategy, position)
    assert amount == -position.stake_amount
    assert -amount * position.amount / position.stake_amount == position.amount
    assert tag == "bounded_full_reduction"


def test_dual_exit_requires_small_layer_and_current_whole_position_net_profit(monkeypatch):
    monkeypatch.setattr(NostalgiaForInfinityX4, "adjust_trade_position", lambda *a, **k: -1.0)
    strategy = NFIBoundedDualExit.__new__(NFIBoundedDualExit)
    positive, state = trade(net=1.0)
    assert adjust(strategy, positive)[0] == -positive.stake_amount
    negative, _ = trade(net=-1.0)
    assert adjust(strategy, negative)[0] == -1.0
    state["sleeve"] = "core"
    assert adjust(strategy, positive)[0] == -1.0


def test_addition_cannot_exceed_first_stake_or_recycle_a_partial_exit(monkeypatch):
    monkeypatch.setattr(NostalgiaForInfinityX4, "adjust_trade_position", lambda *a, **k: 100.0)
    strategy = NFIBoundedPartial.__new__(NFIBoundedPartial)
    position, state = trade()
    assert adjust(strategy, position)[0] == state["first_stake"]
    position.nr_of_successful_exits = 1
    assert adjust(strategy, position) is None
    position.nr_of_successful_exits = 0
    position.funding_fees = -2.0
    assert adjust(strategy, position) is None


def test_initial_stake_reserves_two_fee_inclusive_entry_losses():
    strategy = NFIBoundedPartial.__new__(NFIBoundedPartial)
    strategy.config = {"research_initial_equity_usdt": 1000, "fee": 0.0008}
    strategy.closed_row = lambda pair, when: SimpleNamespace(bounded_atr=0.5)
    stake = strategy.custom_stake_amount(
        "SOL/USDT:USDT", None, 100.0, 100.0, 1.0, 1000.0, 2.0, "102", "long"
    )
    budget = Decimal("0.99")
    two_entries = (
        2 * Decimal(str(stake)) * 2 * (Decimal("0.06") + Decimal("0.0008") * Decimal("1.94"))
    )
    assert two_entries <= budget
    assert float(two_entries / budget) == pytest.approx(0.99)


def test_risk_exit_confirmation_is_not_vetoed():
    strategy = NFIBoundedPartial.__new__(NFIBoundedPartial)
    strategy._remove_profit_target = lambda pair: None
    assert strategy.confirm_trade_exit(
        "SOL/USDT:USDT",
        None,
        "market",
        1.0,
        94.0,
        "gtc",
        "stop_loss",
        datetime(2024, 1, 1, tzinfo=UTC),
    )
