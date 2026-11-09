# ruff: noqa: S101
"""Native wallet budget, profitable cycles and concurrent pending-DCA boundaries."""

from types import SimpleNamespace

import pytest

from deploy.mainnet_100_20261109.capital import CappedWallets, Trade, pending_reservations


@pytest.mark.parametrize("profit,open_stake,expected", [(50, 80, 19), (-40, 50, 9), (-150, 0, 0)])
def test_profit_cannot_expand_ceiling_and_losses_reduce_budget(
    monkeypatch, profit, open_stake, expected
):
    instance = object.__new__(CappedWallets)
    instance._config = {"available_capital": 99}
    instance._stake_currency = "USDT"
    instance._wallets = {"USDT": SimpleNamespace(free=500)}
    monkeypatch.setattr(Trade, "get_total_closed_profit", lambda: profit)
    monkeypatch.setattr(Trade, "total_open_trades_stakes", lambda: open_stake)
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kw: [])
    assert instance.get_available_stake_amount() == expected


def test_pending_initial_is_not_counted_twice_but_dca_is_reserved(monkeypatch):
    initial = SimpleNamespace(
        stake_amount=20,
        nr_of_successful_entries=0,
        entry_side="buy",
        open_orders=[SimpleNamespace(ft_order_side="buy", stake_amount=20)],
    )
    dca = SimpleNamespace(
        stake_amount=50,
        nr_of_successful_entries=1,
        entry_side="buy",
        open_orders=[
            SimpleNamespace(ft_order_side="buy", stake_amount=25),
            SimpleNamespace(ft_order_side="sell", stake_amount=10),
        ],
    )
    assert pending_reservations([initial, dca]) == 25
    instance = object.__new__(CappedWallets)
    instance._config = {"available_capital": 99}
    instance._stake_currency = "USDT"
    instance._wallets = {"USDT": SimpleNamespace(free=400)}
    monkeypatch.setattr(Trade, "get_total_closed_profit", lambda: 80)
    monkeypatch.setattr(Trade, "total_open_trades_stakes", lambda: 70)
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kw: [initial, dca])
    assert instance.get_available_stake_amount() == 4


def test_check_only_never_enters_worker_or_spawns(monkeypatch):
    from deploy.mainnet_100_20261109 import checks, manual

    monkeypatch.setattr(manual, "verify", dict)
    monkeypatch.setattr(checks, "account_checks", lambda: {"methods": ["GET"]})
    monkeypatch.setattr(manual.sys, "argv", ["manual", "--check-only"])
    monkeypatch.setattr(manual, "worker", lambda market: pytest.fail("Live worker invoked"))
    monkeypatch.setattr(manual.subprocess, "Popen", lambda *a, **k: pytest.fail("Process spawned"))
    manual.main()


def test_v3_balances_do_not_need_v2_permission_field():
    from deploy.mainnet_100_20261109.checks import futures_compatible

    mode = {"canTrade": True, "dualSidePosition": False, "multiAssetsMargin": False}
    args = ({"enableFutures": True}, {"positions": []}, [], mode, {"data": {"isLeadTrader": True}})
    assert futures_compatible(*args)
    mode["canTrade"] = False
    assert not futures_compatible(*args)
