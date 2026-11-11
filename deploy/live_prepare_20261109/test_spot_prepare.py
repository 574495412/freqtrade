"""Credential isolation and exchange admission checks; no external requests."""

# ruff: noqa: S101, S105 -- Pytest assertions and dummy credentials.

import pytest

from deploy.live_prepare_20261109.spot_prepare import (
    account_blockers,
    pair_blockers,
    signed_read,
    spot_credentials,
)


def test_spot_never_falls_back_to_futures_credentials():
    values = {"BINANCE_COPYTRADING_API_KEY": "futures", "BINANCE_COPYTRADING_API_SECRET": "f"}
    with pytest.raises(KeyError):
        spot_credentials(values)
    values["BINANCE_COPYTRADING_SPOT_API_KEY"] = "spot"
    with pytest.raises(KeyError):
        spot_credentials(values)
    values["BINANCE_COPYTRADING_SPOT_API_SECRET"] = "s"
    assert spot_credentials(values) == {"exchange": {"key": "spot", "secret": "s"}}


def test_exchange_permission_sets_require_each_group_and_reject_non_usdt():
    info = {
        "symbols": [{
            "symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING",
            "isSpotTradingAllowed": True, "orderTypes": ["LIMIT"],
            "permissionSets": [["SPOT", "MARGIN"], ["TRD_GRP_236", "TRD_GRP_237"]],
        }]
    }
    assert pair_blockers(["BTC/USDT"], ["SPOT", "TRD_GRP_236"], info) == []
    assert pair_blockers(["BTC/USDT"], ["SPOT"], info) == ["BTC/USDT"]
    assert pair_blockers(["BTC/USDC", "BTC/USDT:USDT"], ["SPOT"], info) == [
        "BTC/USDC", "BTC/USDT:USDT"
    ]
    info["symbols"][0]["permissionSets"] = []
    assert pair_blockers(["BTC/USDT"], ["SPOT"], info) == ["BTC/USDT"]


@pytest.mark.parametrize("state", ["funds", "permission", "orders", "inventory", "locked"])
def test_account_admission_rejects_missing_funds_permission_and_unreconciled_state(state):
    permissions = {"enableSpotAndMarginTrading": True}
    account = {"canTrade": True, "balances": [{"asset": "USDT", "free": "500", "locked": "0"}]}
    orders = []
    if state == "funds":
        account["balances"][0]["free"] = "0"
    elif state == "permission":
        permissions["enableSpotAndMarginTrading"] = False
    elif state == "orders":
        orders.append({"orderId": 1})
    elif state == "inventory":
        account["balances"].append({"asset": "BTC", "free": "0.01", "locked": "0"})
    elif state == "locked":
        account["balances"][0]["locked"] = "5"
    assert account_blockers(permissions, account, orders, [])


def test_signed_reader_uses_dedicated_key_get_only_and_redacts_error():
    class Response:
        status_code = 401

        def raise_for_status(self):
            pass

        def json(self):
            return {"serverTime": 100}

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    with pytest.raises(ValueError, match="Spot account read failed: HTTP 401") as error:
        signed_read(session, "account", {"key": "dedicated-spot", "secret": "private-secret"})
    assert "signature" not in str(error.value)
    assert session.calls[1][1]["headers"] == {"X-MBX-APIKEY": "dedicated-spot"}
    assert session.calls[1][1]["allow_redirects"] is False
    with pytest.raises(KeyError):
        signed_read(session, "order", {"key": "dedicated-spot", "secret": "private-secret"})
    assert len(session.calls) == 2


def test_native_capital_budget_excludes_wallet_topups_and_includes_closed_pnl(monkeypatch):
    from freqtrade.persistence import Trade
    from freqtrade.wallets import Wallets

    wallet = object.__new__(Wallets)
    wallet._config = {"available_capital": 495, "tradable_balance_ratio": 0.99}
    wallet._stake_currency = "USDT"
    wallet.get_free = lambda _: 1500  # A later deposit must not increase the native budget.
    monkeypatch.setattr(Trade, "total_open_trades_stakes", lambda: 0)
    monkeypatch.setattr(Trade, "get_total_closed_profit", lambda: -10)
    assert wallet.get_available_stake_amount() == 485
