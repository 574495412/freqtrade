# ruff: noqa: S101
"""Authoritative partial fills, fees, identity errors and zero-fill admission."""

from types import SimpleNamespace

import pytest

from deploy.mainnet_100_20261109 import spot_fills as audit


def fixture():
    local = {
        "id": 1,
        "ft_trade_id": 3,
        "ft_pair": "BTC/USDT",
        "order_id": "42",
        "side": "buy",
        "filled": 0.1,
        "cost": 10,
        "ft_fee_base": 0.0001,
    }
    remote = {
        "orderId": 42,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "executedQty": ".1",
        "cummulativeQuoteQty": "10",
        "status": "FILLED",
    }
    fills = [
        {
            "id": i,
            "orderId": 42,
            "symbol": "BTCUSDT",
            "qty": q,
            "quoteQty": v,
            "commission": fee,
            "commissionAsset": "BTC",
            "isBuyer": True,
        }
        for i, q, v, fee in [(1, ".04", "4", ".00004"), (2, ".06", "6", ".00006")]
    ]
    return local, remote, fills


def test_partial_executions_reconcile_base_fee_and_net_inventory():
    result = audit.reconcile_order(*fixture())
    assert result["passed"]
    assert result["commission_by_asset"] == {"BTC": "0.00010"}
    assert result["filled_qty"] == "0.10"
    assert result["net_base_change_from_fills"] == "0.09990"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("quantity", "NATIVE_FILLED_QTY_MISMATCH"),
        ("fee", "NATIVE_BASE_FEE_MISMATCH"),
        ("duplicate", "DUPLICATE_TRADE_ID"),
        ("wrongorder", "FILL_IDENTITY_MISMATCH"),
    ],
)
def test_non_reconciled_fill_is_never_accepted(change, expected):
    local, remote, fills = fixture()
    if change == "quantity":
        local["filled"] = 0.11
    elif change == "fee":
        local["ft_fee_base"] = 0
    elif change == "duplicate":
        fills.append(fills[0])
    else:
        fills[0]["orderId"] = 43
    result = audit.reconcile_order(local, remote, fills)
    assert not result["passed"] and expected in result["problems"]


def test_order_reader_cannot_select_any_write_action():
    session = SimpleNamespace(get=lambda *a, **k: pytest.fail("Unexpected transport"))
    with pytest.raises(KeyError):
        audit.order_read(session, "place_order", {}, "BTCUSDT", 42)
    with pytest.raises(ValueError):
        audit.order_read(session, "order_query", {}, "BTCUSDT", "not-an-id")


def test_no_native_orders_does_not_query_broker_or_pass_execution(tmp_path, monkeypatch):
    spot = tmp_path / "spot"
    spot.mkdir()
    here = tmp_path / "source"
    here.mkdir()
    cfg = {"trading_mode": "spot", "dry_run": False, "capital_ceiling_usdt": 100}
    monkeypatch.setattr(audit, "STATE", tmp_path)
    monkeypatch.setattr(audit, "HERE", here)
    monkeypatch.setattr(
        audit,
        "read",
        lambda p: cfg if p.name == "config.json" else {"config_sha256": {"spot": "match"}},
    )
    monkeypatch.setattr(audit, "sha", lambda p: "match")
    monkeypatch.setattr(audit, "read_ledger", lambda p: ([], []))
    monkeypatch.setattr(
        audit, "GuardedSession", lambda: pytest.fail("Zero orders must not fetch trades")
    )
    receipt = audit.observe()
    assert receipt["status"] == "WAITING_FOR_FIRST_NATURAL_ORDER"
    assert not receipt["actual_fill_execution_verified"]
    assert receipt["exchange_order_queries"] == 0


@pytest.mark.parametrize("asset", ["USDT", "BNB"])
def test_quote_or_third_asset_fees_are_reported_without_claiming_native_fee_match(asset):
    local, remote, fills = fixture()
    local["ft_fee_base"] = 0
    for fill in fills:
        fill["commissionAsset"] = asset
    result = audit.reconcile_order(local, remote, fills)
    assert result["passed"]
    assert asset in result["commission_by_asset"]
    assert not result["native_fee_components_reconciled"]


@pytest.mark.parametrize("variant", ["match", "missing_base_fee", "untracked_asset"])
def test_exchange_fills_native_positions_and_account_holdings_must_all_agree(variant):
    local, remote, fills = fixture()
    result = audit.reconcile_order(local, remote, fills)
    native_amount = ".09990" if variant != "missing_base_fee" else ".1"
    trades = [{"id": 3, "pair": "BTC/USDT", "is_open": True, "amount": native_amount}]
    account = {
        "balances": [
            {"asset": "USDT", "free": "490", "locked": "0"},
            {"asset": "BTC", "free": ".09990", "locked": "0"},
        ]
    }
    if variant == "untracked_asset":
        account["balances"].append({"asset": "BNB", "free": ".01", "locked": "0"})
    differences = audit.position_differences(account, [local], trades, [local], [result])
    assert bool(differences) == (variant != "match")
