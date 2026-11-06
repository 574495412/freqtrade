"""Offline regression against the inherited X8 callback and native Order fields."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from freqtrade.persistence import Order


STRATEGY = Path(__file__).parent / "strategy/NFIX8AddCapPaper.py"
spec = importlib.util.spec_from_file_location("nfi_x8_addcap_test_strategy", STRATEGY)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Candidate = module.NFIX8AddCapPaper
Baseline = module.NostalgiaForInfinityX8


def order(filled=1, price=100, *, side="buy", pending=False):
    return Order(
        ft_order_side=side,
        ft_is_open=pending,
        status="open" if pending else "closed",
        filled=filled,
        average=price,
    )


def evaluate(monkeypatch, orders, proposal, *, tag="1", short=False, minimum=0, leverage=1):
    monkeypatch.setattr(Baseline, "adjust_trade_position", lambda self, *a, **k: proposal)
    strategy = object.__new__(Candidate)
    trade = SimpleNamespace(
        enter_tag=tag,
        is_short=short,
        leverage=leverage,
        orders=orders,
        entry_side="sell" if short else "buy",
    )
    return strategy.adjust_trade_position(trade, None, 100, 0, minimum, 1000, 100, 100, 0, 0)


@pytest.mark.parametrize("proposal", [None, (None, None), 0, (-35, "derisk")])
def test_inherited_noop_and_reductions_pass_through(monkeypatch, proposal):
    assert evaluate(monkeypatch, [], proposal) == proposal


def test_only_pure_long_top_coins_is_exempt(monkeypatch):
    assert evaluate(monkeypatch, [], (200, "grind"), tag="141 145") == (200, "grind")
    assert evaluate(monkeypatch, [], (200, "grind"), tag="141 1") is None
    assert evaluate(monkeypatch, [], 200, tag="562", short=True) is None


def test_addition_exactly_at_room_preserves_order_tag(monkeypatch):
    assert evaluate(monkeypatch, [order(), order(0.6)], (40, "grind")) == (40, "grind")
    assert evaluate(monkeypatch, [order(), order(0.6)], 40.0001) is None


def test_third_addition_rejected_after_reduction(monkeypatch):
    orders = [order(), order(0.1), order(0.1), order(0.7, side="sell")]
    assert evaluate(monkeypatch, orders, 10) is None


def test_pending_partial_and_zero_fill_cancel(monkeypatch):
    pending = order(0.1, pending=True)
    assert evaluate(monkeypatch, [order(), pending], 10) is None
    pending.ft_is_open = False
    pending.status = "canceled"
    canceled = order(0)
    canceled.status = "canceled"
    assert evaluate(monkeypatch, [canceled, order(), pending], 90) == 90
    assert evaluate(monkeypatch, [canceled, order(), pending], 90.01) is None


def test_reduction_does_not_restore_addition_amount(monkeypatch):
    assert evaluate(monkeypatch, [order(), order(0.5), order(0.9, side="sell")], 60) is None


def test_exchange_minimum_is_not_rounded_up_by_the_wrapper(monkeypatch):
    assert evaluate(monkeypatch, [order(), order(0.95)], 4, minimum=5) is None
    assert evaluate(monkeypatch, [order(), order(0.95)], 5, minimum=5) == 5


def test_short_uses_sell_fills_and_paper_leverage_contract(monkeypatch):
    orders = [order(side="sell"), order(0.25, side="sell"), order(0.5, side="buy")]
    assert evaluate(monkeypatch, orders, 75, tag="562", short=True) == 75
    assert evaluate(monkeypatch, orders, 75, tag="562", short=True, leverage=2) is None


def test_invalid_history_and_nonfinite_stake_fail_closed(monkeypatch):
    assert evaluate(monkeypatch, [order(price=0)], 1) is None
    assert evaluate(monkeypatch, [order()], float("nan")) is None
