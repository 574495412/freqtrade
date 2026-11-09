# ruff: noqa: S101
"""Spot observation never reaches trade endpoints and rejects ambiguous admission."""

from types import SimpleNamespace

import pytest

from deploy.mainnet_100_20261109.spot_status import account_blockers, pair_blockers, signed_read


def test_unknown_endpoint_is_rejected_before_any_transport():
    session = SimpleNamespace(get=lambda *a, **k: pytest.fail("Unexpected request"))
    with pytest.raises(KeyError):
        signed_read(session, "order", {"key": "dummy", "secret": "dummy"})


@pytest.mark.parametrize(
    "status,fail_time", [(418, False), (429, False), (401, False), (418, True)]
)
def test_http_failure_is_not_retried_and_never_exposes_signed_url(status, fail_time):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        code = status if fail_time or len(calls) > 1 else 200
        return SimpleNamespace(status_code=code, json=lambda: {"serverTime": 123})

    with pytest.raises(ValueError) as error:
        signed_read(
            SimpleNamespace(get=get), "account", {"key": "dedicated-spot", "secret": "dummy"}
        )
    assert len(calls) == (1 if fail_time else 2)
    assert str(status) in str(error.value)
    assert "signature" not in str(error.value) and "https://" not in str(error.value).lower()
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)
    if not fail_time:
        assert calls[1][1]["headers"] == {"X-MBX-APIKEY": "dedicated-spot"}


def test_symbol_permission_groups_are_and_groups_of_or_choices():
    info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "orderTypes": ["LIMIT"],
                "permissionSets": [["SPOT", "MARGIN"], ["TRD_GRP_1", "TRD_GRP_2"]],
            }
        ]
    }
    assert not pair_blockers(["BTC/USDT"], ["SPOT", "TRD_GRP_2"], info)
    assert pair_blockers(["BTC/USDT"], ["SPOT"], info) == ["BTC/USDT"]
    assert pair_blockers(["BTC/USDT:USDT"], ["SPOT", "TRD_GRP_2"], info) == ["BTC/USDT:USDT"]


@pytest.mark.parametrize("state", ["inventory", "locked", "orders", "permission"])
def test_active_or_ambiguous_account_cannot_pass_flat_state_observation(state):
    permissions = {"enableSpotAndMarginTrading": True}
    account = {"canTrade": True, "balances": [{"asset": "USDT", "free": "500", "locked": "0"}]}
    orders = []
    if state == "inventory":
        account["balances"].append({"asset": "BTC", "free": "0.001", "locked": "0"})
    elif state == "locked":
        account["balances"][0]["locked"] = "10"
    elif state == "orders":
        orders.append({"orderId": 1})
    else:
        account["canTrade"] = False
    assert account_blockers(permissions, account, orders, [])
